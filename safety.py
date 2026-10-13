"""Strategy 3 safety gate: pass/fail checks, each with the value found.

Solana tokens are checked with RugCheck; EVM tokens with Sharpe's rug-check. Cluster, bundle
and creator rug-pull checks use OKX (and MadeOnSol's bundle data on Solana) when keys are set.
A check the source doesn't offer for that chain is "manual" (listed in the email
for you to do by hand). A check the source should answer but didn't is "unknown",
which fails the coin when `unknown_counts_as_fail` is on.
"""

from dataclasses import dataclass

import sources

DEAD_PREFIXES = ("0x000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead")


@dataclass
class Check:
    name: str
    status: str  # pass | fail | unknown | manual
    value: str

    @property
    def ok(self):
        return self.status in ("pass", "manual")


def _limit(name, value, limit, unit="%"):
    """Pass when value < limit; unknown when value is missing."""
    if value is None:
        return Check(name, "unknown", "no data")
    return Check(name, "pass" if value < limit else "fail", f"{value:.1f}{unit} (limit {limit}{unit})")


def _offchain(address, chain_index, s):
    """Cluster, bundle and creator rug-pull checks. OKX answers all three when its key is set;
    without it, MadeOnSol's free bundle data covers Solana. Anything left is listed as manual.
    chain_index is OKX's id: "501" for Solana, the EVM chain id otherwise."""
    okx, solana = sources.okx_enabled(), chain_index == "501"

    # A failed lookup falls back to a manual check (as before these were automated) rather than
    # failing the coin: OKX's cluster data can error on brand-new tokens, which Strategy 2 targets.
    def ask(name, fn):
        try:
            return fn(), None
        except sources.DataError as e:
            return None, Check(name, "manual", f"lookup failed ({str(e)[:80]}): check by hand")

    out = []
    name = "Largest connected cluster"
    if okx:
        largest, err = ask(name, lambda: sources.okx_largest_cluster(address, chain_index))
        out.append(err or (_limit(name, largest, s["max_cluster_pct"]) if largest is not None
                           else Check(name, "manual", "OKX has no cluster data yet: check the Bubblemaps map")))
    else:
        out.append(Check(name, "manual", "needs an OKX key: check the Bubblemaps map"))

    name = "Bundled supply at launch"
    if okx:
        peak, err = ask(name, lambda: sources.okx_bundle(address, chain_index))
        out.append(err or (_limit(name, peak, s["max_bundled_pct"]) if peak is not None
                           else Check(name, "manual", "OKX has no launch data (not a meme-launchpad token)")))
    elif solana and sources.madeonsol_enabled():
        held, err = ask(name, lambda: sources.mos_bundle(address))
        out.append(err or (_limit("Bundled supply still held", held, s["max_bundled_pct"]) if held is not None
                           else Check(name, "manual", "MadeOnSol has no launch-bundle data for this token")))
    else:
        out.append(Check(name, "manual", "needs an OKX key: check bundles on Bubblemaps or GMGN"))

    name = "Creator rug pulls"
    if okx:
        dev, err = ask(name, lambda: sources.okx_dev(address, chain_index))
        if err:
            out.append(err)
        elif dev is None:
            out.append(Check(name, "manual", "OKX has no creator data (not a meme-launchpad token)"))
        else:
            rugs, launched = dev
            out.append(Check(name, "pass" if rugs == 0 else "fail", f"{rugs} rug pulls in {launched} launches"))
    return out


def check_token(chain, address, s):
    """Returns (checks, snapshot). snapshot feeds post-buy holder monitoring."""
    if not chain or not address:
        return [Check("Token contract", "unknown", "native coin or no contract on CoinGecko: nothing to scan")], {}
    if chain == "solana":
        return _solana(address, s)
    evm_id = sources.CHAINS.get(chain, (None, None))[1]
    if evm_id:
        return _evm(address, evm_id, s)
    return [Check("Token contract", "unknown", f"no free safety scanner for chain '{chain}'")], {}


def _solana(mint, s):
    r = sources.rugcheck_report(mint)
    if not r or not r.get("token"):
        return [Check("RugCheck report", "unknown", "RugCheck could not build a report for this token")], {}

    token = r["token"]
    checks = [
        Check("Mint authority", "pass" if token.get("mintAuthority") is None else "fail",
              "revoked" if token.get("mintAuthority") is None else "ACTIVE"),
        Check("Freeze authority", "pass" if token.get("freezeAuthority") is None else "fail",
              "revoked" if token.get("freezeAuthority") is None else "ACTIVE"),
    ]

    # Liquidity: liquidity-weighted share of LP that is burned or locked, across pools.
    weighted, total = 0.0, 0.0
    for m in r.get("markets") or []:
        lp = m.get("lp") or {}
        liq = (lp.get("baseUSD") or 0) + (lp.get("quoteUSD") or 0)
        if liq and lp.get("lpLockedPct") is not None:
            weighted += lp["lpLockedPct"] * liq
            total += liq
    locked = weighted / total if total else None
    if locked is None:
        checks.append(Check("Liquidity burned/locked", "unknown", "no pool data"))
    else:
        checks.append(Check("Liquidity burned/locked", "pass" if locked >= s["min_lp_locked_pct"] else "fail",
                            f"{locked:.1f}% (need {s['min_lp_locked_pct']}%+)"))

    # Holders, leaving out pools and lockers so the AMM's own balance isn't counted.
    known = r.get("knownAccounts") or {}
    skip = {a for a, k in known.items() if k.get("type") in ("AMM", "LOCKER")}
    skip |= {m.get("pubkey") for m in r.get("markets") or []}
    holders = [h for h in r.get("topHolders") or [] if h.get("owner") not in skip and h.get("address") not in skip]
    pcts = sorted((h.get("pct") or 0 for h in holders), reverse=True)
    top10 = sum(pcts[:10]) if pcts else None
    checks.append(_limit("Largest single holder", pcts[0] if pcts else None, s["max_single_holder_pct"]))
    checks.append(_limit("Top 10 holders", top10, s["max_top10_holders_pct"]))

    # Insiders: share held by top holders RugCheck flags as insiders. Its "insiderNetworks"
    # totals are not used: on big tokens one network can span thousands of wallets and
    # report more than the whole supply.
    insider = sum(h.get("pct") or 0 for h in holders if h.get("insider")) if holders else None
    checks.append(_limit("Insider wallets", insider, s["max_insider_pct"]))

    rug_risks = [x["name"] for x in r.get("risks") or [] if "rug" in x.get("name", "").lower()]
    if r.get("rugged") or rug_risks:
        checks.append(Check("Creator rug history", "fail", "; ".join(rug_risks) or "token marked rugged"))
    else:
        checks.append(Check("Creator rug history", "pass", "none flagged by RugCheck"))

    checks += _offchain(mint, "501", s)
    snapshot = {"top10_pct": top10, "creator_balance": r.get("creatorBalance")}
    return checks, snapshot


def _evm(address, chain_id, s):
    r = sources.evm_security(address, chain_id)
    if not r or "noMint" not in r:
        return [Check("Security scan", "unknown", "Sharpe rug-check returned no data")], {}

    by_label = {c.get("label"): c for c in r.get("checks") or []}
    checks = []

    no_mint = r.get("noMint")
    checks.append(Check("Mint authority", "unknown" if no_mint is None else ("pass" if no_mint else "fail"),
                        "no data" if no_mint is None else ("disabled" if no_mint else "ACTIVE")))

    freeze = by_label.get("Freeze / Blacklist")
    if freeze is None:
        checks.append(Check("Freeze / blacklist", "unknown", "no data"))
    else:
        checks.append(Check("Freeze / blacklist", "pass" if freeze.get("status") in ("safe", "neutral") else "fail",
                            str(freeze.get("value"))))

    honeypot = r.get("isHoneypot") or r.get("cannotSellAll")
    checks.append(Check("Honeypot / sell block", "fail" if honeypot else "pass", "YES" if honeypot else "no"))

    lp = by_label.get("LP Burnt/Locked")
    try:
        locked = float(str(lp.get("value")).rstrip("%")) if lp else None
    except ValueError:
        locked = None
    if locked is None:
        checks.append(Check("Liquidity burned/locked", "unknown", "no data"))
    else:
        checks.append(Check("Liquidity burned/locked", "pass" if locked >= s["min_lp_locked_pct"] else "fail",
                            f"{locked:.1f}% (need {s['min_lp_locked_pct']}%+)"))

    pairs = {(d.get("pair") or "").lower() for d in r.get("dexInfo") or []}
    holders = [h for h in r.get("topHolders") or []
               if not h.get("isLocked")
               and (h.get("address") or "").lower() not in pairs
               and not (h.get("address") or "").lower().startswith(DEAD_PREFIXES)]
    pcts = sorted(((h.get("percent") or 0) * 100 for h in holders), reverse=True)
    top10 = sum(pcts[:10]) if pcts else None
    checks.append(_limit("Largest single holder", pcts[0] if pcts else None, s["max_single_holder_pct"]))
    checks.append(_limit("Top 10 holders", top10, s["max_top10_holders_pct"]))

    checks.append(Check("Insider wallets", "manual", "no free insider graph for EVM chains: check Bubblemaps"))
    same_creator = r.get("honeypotWithSameCreator")
    checks.append(Check("Creator rug history", "fail" if same_creator else "pass",
                        "creator deployed a honeypot before" if same_creator else "none flagged"))

    checks += _offchain(address, str(chain_id), s)
    snapshot = {"top10_pct": top10, "creator_balance": r.get("creatorPercent")}
    return checks, snapshot


def passed(checks, unknown_counts_as_fail):
    for c in checks:
        if c.status == "fail" or (c.status == "unknown" and unknown_counts_as_fail):
            return False
    return True
