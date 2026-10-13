"""Data layer. Sharpe, CoinGecko, DexScreener and RugCheck are free and keyless.
MadeOnSol (KOL wallets, bundles) and OKX (clusters, bundles, creator rug pulls) are used
only when their keys are set as environment variables."""

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import requests

SHARPE = "https://www.sharpe.ai/api"
COINGECKO = "https://api.coingecko.com/api/v3"
DEXSCREENER = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1"
MADEONSOL = "https://madeonsol.com/api/v1"
OKX = "https://web3.okx.com"

# Seconds between calls per host, to stay inside each free tier.
# Sharpe rug-check is 20/min; CoinGecko's keyless tier is roughly 5–15/min;
# MadeOnSol's free tier bursts to 60/min within 200/day.
MIN_GAP = {
    "www.sharpe.ai": 0.6,
    "api.coingecko.com": 6.0,
    "api.dexscreener.com": 0.3,
    "api.rugcheck.xyz": 1.0,
    "madeonsol.com": 1.1,
    "web3.okx.com": 0.5,
}

# CoinGecko platform id -> OKX chainIndex
OKX_CHAINS = {
    "solana": "501", "ethereum": "1", "binance-smart-chain": "56", "base": "8453",
    "arbitrum-one": "42161", "polygon-pos": "137", "avalanche": "43114",
}

# CoinGecko platform id -> (DexScreener chain id, EVM chain id for Sharpe rug-check)
CHAINS = {
    "solana": ("solana", None),
    "ethereum": ("ethereum", 1),
    "binance-smart-chain": ("bsc", 56),
    "base": ("base", 8453),
    "arbitrum-one": ("arbitrum", 42161),
    "polygon-pos": ("polygon", 137),
    "avalanche": ("avalanche", 43114),
}

_session = requests.Session()
_session.headers["User-Agent"] = "meme-signal-bot/1.0 (alert-only)"
_last_call: dict[str, float] = {}
last_headers: dict[str, dict] = {}  # host -> headers of its latest response (rate-limit counters)


class DataError(Exception):
    """A source failed or returned stale data; the run skips instead of guessing."""


def get_json(url, params=None, tries=3, ok_missing=False, headers=None):
    """headers: a dict, or a function returning one (for signatures that must be fresh per attempt)."""
    host = urlparse(url).netloc
    for attempt in range(tries):
        wait = _last_call.get(host, 0) + MIN_GAP.get(host, 0.5) - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = _session.get(url, params=params, timeout=30,
                             headers=headers() if callable(headers) else headers)
        except requests.RequestException as e:
            err = str(e)
        else:
            last_headers[host] = r.headers
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:  # e.g. a bot-check page served with status 200
                    err = f"HTTP 200 but not JSON: {r.text[:120]!r}"
                    time.sleep(3 * (attempt + 1))
                    continue
            if ok_missing and r.status_code in (400, 404):
                return None
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", "20") or 20), 60))
                continue
            if r.status_code < 500:
                break
        time.sleep(3 * (attempt + 1))
    raise DataError(f"{host}: {err}")


# --- Sharpe -----------------------------------------------------------------

def memecoins(narrative=None):
    """Narrative overview, or one narrative's coins when `narrative` is a slug."""
    d = get_json(f"{SHARPE}/memecoins/data", {"narrative": narrative} if narrative else None)
    if d.get("degraded") or (d.get("freshness") or {}).get("stale"):
        raise DataError("Sharpe memecoin data is stale or degraded")
    return d


def evm_security(address, chain_id):
    """Sharpe rug-check (GoPlus-backed). EVM chains only."""
    return get_json(f"{SHARPE}/rug-check/security",
                    {"address": address, "chainId": chain_id}, ok_missing=True)


# --- CoinGecko --------------------------------------------------------------

def global_market():
    d = get_json(f"{COINGECKO}/global")["data"]
    return {
        "total_mcap_usd": d["total_market_cap"]["usd"],
        "mcap_change_24h_pct": d["market_cap_change_percentage_24h_usd"],
    }


def coin_contract(coingecko_id):
    """(chain, address) for a coin's native chain, or (None, None) for native coins like DOGE."""
    d = get_json(f"{COINGECKO}/coins/{coingecko_id}", {
        "localization": "false", "tickers": "false", "market_data": "false",
        "community_data": "false", "developer_data": "false", "sparkline": "false",
    }, ok_missing=True) or {}
    chain = d.get("asset_platform_id")
    address = (d.get("platforms") or {}).get(chain) if chain else None
    return (chain, address) if address else (None, None)


def prices_by_id(ids):
    if not ids:
        return {}
    d = get_json(f"{COINGECKO}/simple/price", {"ids": ",".join(sorted(set(ids))), "vs_currencies": "usd"})
    return {k: v.get("usd") for k, v in d.items()}


# --- DexScreener ------------------------------------------------------------

def dex_pair(chain, address):
    """The highest-liquidity DEX pair for a token, or None."""
    dex_chain = CHAINS.get(chain, (None, None))[0]
    if not dex_chain:
        return None
    pairs = get_json(f"{DEXSCREENER}/tokens/v1/{dex_chain}/{address}", ok_missing=True) or []
    pairs = [p for p in pairs if (p.get("liquidity") or {}).get("usd")]
    if not pairs:
        return None
    best = max(pairs, key=lambda p: p["liquidity"]["usd"])
    return {
        "price_usd": float(best["priceUsd"]) if best.get("priceUsd") else None,
        "liquidity_usd": best["liquidity"]["usd"],
        "url": best.get("url"),
    }


# --- RugCheck ---------------------------------------------------------------

def rugcheck_report(mint):
    """Full RugCheck report for a Solana mint. None when RugCheck can't build one
    (it often can't for large, older tokens)."""
    return get_json(f"{RUGCHECK}/tokens/{mint}/report", ok_missing=True)


# --- MadeOnSol (MADEONSOL_API_KEY; free tier: 200 calls/day, feeds 5 min delayed) ---

def madeonsol_enabled():
    return bool(os.environ.get("MADEONSOL_API_KEY", "").strip())


def _mos(path, params=None, ok_missing=False):
    key = os.environ.get("MADEONSOL_API_KEY", "").strip()
    return get_json(f"{MADEONSOL}{path}", params, ok_missing=ok_missing,
                    headers={"Authorization": f"Bearer {key}"})


def madeonsol_calls_left():
    """Calls left in today's quota, from the last response's headers (None if unknown)."""
    v = (last_headers.get("madeonsol.com") or {}).get("X-RateLimit-Remaining")
    return int(v) if v and str(v).isdigit() else None


def mos_me():
    return _mos("/me")


def mos_leaderboard(period, limit=100):
    return (_mos("/kol/leaderboard", {"period": period, "limit": limit}) or {}).get("leaderboard") or []


def mos_kol_pnl(wallet, period):
    return _mos(f"/kol/{wallet}/pnl", {"period": period}, ok_missing=True) or {}


def mos_feed(**params):
    """One page of the KOL trade feed, newest first. Pass since= (poll cursor) and cursor= (paging)."""
    return _mos("/kol/feed", {k: v for k, v in params.items() if v is not None})


def mos_kol_token(mint):
    """KOL buying and selling on one token."""
    return _mos(f"/kol/tokens/{mint}", ok_missing=True) or {}


def mos_bundle(mint):
    """Share of supply (0-100) still held by the launch-bundle cohort, or None when not covered."""
    d = _mos(f"/tokens/{mint}/bundle", ok_missing=True) or {}
    if (d.get("coverage") or {}).get("in_scope") is False:
        return None
    held = (d.get("bundle") or {}).get("held_pct_of_supply")
    return None if held is None else float(held) * 100


# --- OKX Onchain OS (OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE; optional OKX_PROJECT_ID) ---

def okx_enabled():
    return all(os.environ.get(k, "").strip() for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))


def _okx(path, params):
    """Signed GET. Returns the response's `data` with a list unwrapped to its first item;
    None when OKX has no data for the token."""
    request_path = f"{path}?{urlencode(params)}"

    def headers():
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        secret = os.environ["OKX_SECRET_KEY"].strip().encode()
        sign = base64.b64encode(hmac.new(secret, f"{ts}GET{request_path}".encode(), hashlib.sha256).digest())
        h = {"OK-ACCESS-KEY": os.environ["OKX_API_KEY"].strip(), "OK-ACCESS-SIGN": sign.decode(),
             "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": os.environ["OKX_PASSPHRASE"].strip()}
        if os.environ.get("OKX_PROJECT_ID", "").strip():
            h["OK-ACCESS-PROJECT"] = os.environ["OKX_PROJECT_ID"].strip()
        return h

    d = get_json(f"{OKX}{request_path}", headers=headers)
    if str(d.get("code")) != "0":
        raise DataError(f"OKX {d.get('code')}: {d.get('msg') or d}")
    data = d.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    return data or None


def _pct(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def okx_largest_cluster(address, chain_index):
    """Largest share of supply (0-100) held by one connected-wallet cluster, leaving out
    clusters made only of exchange wallets. None when OKX has no cluster data."""
    d = _okx("/api/v6/dex/market/token/cluster/list",
             {"chainIndex": chain_index, "tokenContractAddress": address})
    if not d:
        return None
    shares = [_pct(c.get("holdingPercent")) for c in d.get("clusterList") or []
              if not all(a.get("isExchange") for a in c.get("clusterAddressList") or [{}])]
    shares = [x for x in shares if x is not None]
    return max(shares) if shares else 0.0


def okx_bundle(address, chain_index):
    """Peak share of supply (0-100) held by launch bundlers. None outside meme launchpads."""
    d = _okx("/api/v6/dex/market/memepump/tokenBundleInfo",
             {"chainIndex": chain_index, "tokenContractAddress": address})
    return _pct(d.get("bundlerAthPercent")) if d else None


def okx_dev(address, chain_index):
    """(rug pulls, tokens launched) for the token's creator. None outside meme launchpads."""
    d = _okx("/api/v6/dex/market/memepump/tokenDevInfo",
             {"chainIndex": chain_index, "tokenContractAddress": address})
    info = (d or {}).get("devLaunchedInfo") or {}
    rugs = _pct(info.get("rugPullCount"))
    return None if rugs is None else (int(rugs), int(_pct(info.get("totalTokens")) or 0))
