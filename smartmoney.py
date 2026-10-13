"""Strategy 2: alerts when several vetted smart-money wallets buy the same coin.

Runs on MadeOnSol's free tier (200 calls a day, KOL feed delayed 5 minutes):
- weekly: KOL leaderboard -> each candidate's 30d win rate and profit factor -> data/watchlist.csv
- hourly: the whole KOL buy feed since the last run (about 3–4 calls), kept for the cluster window
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import sources


def _t(s):
    t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(x):
    """Win rates may come as 0–1 or 0–100; return 0–100."""
    if x is None:
        return None
    x = float(x)
    return x * 100 if 0 < x <= 1 else x


def watchlist_due(rows, now, s):
    if not rows:
        return True
    try:
        return now - _t(rows[0]["built_utc"]) >= timedelta(days=s["rebuild_watchlist_days"])
    except (KeyError, ValueError):
        return True


def build_watchlist(now, s):
    """Vet the top KOLs by PnL; keep the best by profit factor. Returns (rows, Counter of drop reasons)."""
    period = s["leaderboard_period"]
    board = [e for e in sources.mos_leaderboard(period) if e.get("wallet")]
    board.sort(key=lambda e: e.get("pnl") or 0, reverse=True)
    kept, dropped = [], Counter()
    for e in board[: s["vet_candidates"]]:
        summ = sources.mos_kol_pnl(e["wallet"], period).get("summary") or {}
        wr, pf = _pct(summ.get("win_rate")), summ.get("profit_factor")
        closed = summ.get("closed_positions") or 0
        if wr is None or pf is None:
            dropped["no PnL data"] += 1
        elif wr >= s["max_win_rate_pct"]:
            dropped[f"win rate {s['max_win_rate_pct']}%+ (bot-like)"] += 1
        elif closed < s["min_closed_positions"]:
            dropped["too few trades"] += 1
        elif float(pf) < s["min_profit_factor"]:
            dropped["low profit factor"] += 1
        else:
            kept.append({
                "built_utc": _iso(now), "wallet": e["wallet"], "name": e.get("name") or e["wallet"][:6],
                "win_rate_pct": round(wr, 1), "profit_factor": round(float(pf), 2),
                "pnl_sol": round(float(summ.get("total_pnl_sol") or e.get("pnl") or 0), 1),
                "closed_positions": closed,
            })
    kept.sort(key=lambda r: r["profit_factor"], reverse=True)
    return kept[: s["watchlist_size"]], dropped


def _buy(t):
    return {
        "wallet": t["wallet_address"], "name": t.get("kol_name") or t["wallet_address"][:6],
        "mint": t["token_mint"], "symbol": t.get("token_symbol") or "?", "token_name": t.get("token_name") or "",
        "sol": t.get("sol_amount"), "mc_usd": t.get("market_cap_usd_at_trade"),
        "price_usd": t.get("price_usd_at_trade"), "at": t["traded_at"], "tx": t.get("tx_signature"),
    }


def poll_buys(st, wallets, now, s):
    """Add watchlist wallets' buys since the last run to st["buys"], dropping buys older than
    the cluster window. Returns a one-line note for the run log."""
    window_start = now - timedelta(hours=s["window_hours"])
    buys = st.setdefault("buys", [])
    seen = {b["tx"] for b in buys}

    def keep(rows):
        n = 0
        for t in rows:
            if t.get("wallet_address") in wallets and t.get("action", "buy") == "buy" \
                    and t.get("token_mint") and t.get("tx_signature") not in seen:
                buys.append(_buy(t))
                seen.add(t.get("tx_signature"))
                n += 1
        return n

    added = 0
    # Wallets new to the watchlist get their own window of history (one call each),
    # so a freshly built watchlist doesn't start blind.
    done = set(st.get("backfilled", []))
    for w in sorted(wallets - done):
        added += keep(sources.mos_feed(kol=w, action="buy", since=_iso(window_start), limit=100).get("trades") or [])
        done.add(w)
    st["backfilled"] = sorted(done & wallets)

    since = st.get("since") or _iso(now - timedelta(hours=1))
    cursor, newest, pages, capped = None, None, 0, False
    while True:
        d = sources.mos_feed(action="buy", since=since, cursor=cursor, limit=100)
        pages += 1
        newest = newest or d.get("next_since")
        added += keep(d.get("trades") or [])
        if not d.get("has_more") or not d.get("next_cursor"):
            break
        left = sources.madeonsol_calls_left()
        if pages >= s["feed_max_pages_per_run"] or (left is not None and left < s["calls_reserve"]):
            capped = True
            break
        cursor = d["next_cursor"]
    if newest:
        st["since"] = newest
    st["buys"] = [b for b in buys if _t(b["at"]) >= window_start]
    note = f"{added} new watchlist buys from {pages} feed page(s)"
    if capped:
        note += " (paging capped: some older buys this hour were skipped)"
    return note


def clusters(st, s):
    """{mint: buys} for coins bought by at least min_cluster_wallets distinct watchlist wallets."""
    by_mint = defaultdict(list)
    for b in st.get("buys", []):
        by_mint[b["mint"]].append(b)
    return {m: sorted(bs, key=lambda b: b["at"]) for m, bs in by_mint.items()
            if len({b["wallet"] for b in bs}) >= s["min_cluster_wallets"]}


def watchlist_sellers(mint, wallets, since):
    """Watchlist wallets that have sold this coin, with their last trade after `since`."""
    out = []
    for k in sources.mos_kol_token(mint).get("kols") or []:
        if k.get("wallet") in wallets and (k.get("sell_count") or 0) > 0 and k.get("last_trade"):
            if _t(k["last_trade"]) >= since:
                out.append(k)
    return out
