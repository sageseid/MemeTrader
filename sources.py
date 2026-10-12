"""Data layer: Sharpe, CoinGecko, DexScreener and RugCheck, all free and keyless."""

import time
from urllib.parse import urlparse

import requests

SHARPE = "https://www.sharpe.ai/api"
COINGECKO = "https://api.coingecko.com/api/v3"
DEXSCREENER = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1"

# Seconds between calls per host, to stay inside each free tier.
# Sharpe rug-check is 20/min; CoinGecko's keyless tier is roughly 5–15/min.
MIN_GAP = {
    "www.sharpe.ai": 0.6,
    "api.coingecko.com": 6.0,
    "api.dexscreener.com": 0.3,
    "api.rugcheck.xyz": 1.0,
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


class DataError(Exception):
    """A source failed or returned stale data; the run skips instead of guessing."""


def get_json(url, params=None, tries=3, ok_missing=False):
    host = urlparse(url).netloc
    for attempt in range(tries):
        wait = _last_call.get(host, 0) + MIN_GAP.get(host, 0.5) - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = _session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200:
                return r.json()
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
