#!/usr/bin/env python3
"""Meme Narrative Signal Bot: alert-only. It never trades and never holds keys.

  python bot.py               hourly pass: buy candidates, exit alerts, weekly scorecard
  python bot.py --dry-run     same pass, but print emails instead of sending them
  python bot.py --test-email  send one test email (the first one triggers FormSubmit activation)
  python bot.py --scorecard   send the weekly scorecard now
"""

import argparse
import os
import sys
import tomllib
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import emailer
import safety
import sources
import store
import strategy

ROOT = Path(__file__).parent
FOOTER = ("Not financial advice. Alert-only: the bot never trades or holds keys. "
          "No links on purpose: buy only through sites you have bookmarked.")
MANUAL_ALWAYS = [
    "Copycat ticker: confirm this is the official contract, not a copy of a trending ticker",
    "Socials: X account age, follower quality, bot-farmed Telegram",
    "Bubblemaps map shape: galaxy vs. solar system",
]


def money(x):
    if x is None:
        return "n/a"
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.1f}K"
    if x >= 0.01:
        return f"${x:.4f}"
    return f"${x:.12f}".rstrip("0")


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s):
    t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class Bot:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        g = cfg["general"]
        self.paper = g["mode"].lower() != "live"
        self.mail = {
            "to": os.environ.get("ALERT_TO") or g["alert_to"],
            # Accepts the bare id or the full endpoint (https://formspree.io/f/<id>).
            "formspree_form": (os.environ.get("FORMSPREE_FORM") or g.get("formspree_form", ""))
                              .strip().rstrip("/").rsplit("/", 1)[-1],
            "formsubmit_referer": g["formsubmit_referer"],
        }
        self.dry_run = dry_run
        self.state = store.load_state()
        self.t = datetime.now(timezone.utc)
        self.email_errors = []

    # --- helpers -------------------------------------------------------------

    def email(self, subject, fields):
        tag = "[PAPER] " if self.paper else ""
        try:
            emailer.send(tag + subject, fields, self.mail, self.dry_run)
            return True
        except emailer.EmailError as e:
            print(f"EMAIL FAILED: {e}", file=sys.stderr)
            self.email_errors.append(str(e))
            return False

    def log_run(self, outcome, detail):
        print(f"[{iso(self.t)}] {outcome}: {detail}")
        if not self.dry_run:
            store.log_run({"run_utc": iso(self.t), "mode": "PAPER" if self.paper else "LIVE",
                           "outcome": outcome, "detail": detail})

    def contract(self, coingecko_id):
        cache = self.state.setdefault("contracts", {})
        if coingecko_id not in cache:
            cache[coingecko_id] = list(sources.coin_contract(coingecko_id))
        return tuple(cache[coingecko_id])

    def size_text(self):
        g = self.cfg["general"]
        pct = g["position_size_pct"]
        if g["capital_usd"]:
            return f"{pct}% of trading capital = {money(g['capital_usd'] * pct / 100)}"
        return f"{pct}% of trading capital (set capital_usd in settings.toml to show the amount)"

    # --- hourly pass ---------------------------------------------------------

    def run(self):
        week_ago = self.t - timedelta(days=7)
        snaps = self.state.get("signal_snapshots", {})
        for coin in [k for k, v in snaps.items() if parse_iso(v["at"]) < week_ago]:
            del snaps[coin]

        overview = ranked = None
        try:
            overview = sources.memecoins()
            ranked = strategy.rank_narratives(overview, self.cfg["narratives"])
            self.signal_pass(overview, ranked)
        except sources.DataError as e:
            self.log_run("data error", f"skipped this run's signal pass: {e}")
        self.check_positions(overview, ranked)
        self.maybe_scorecard()

    def signal_pass(self, overview, ranked):
        n = self.cfg["narratives"]
        ranking = ", ".join(f"{r['name']} {r['change7d']:+.1f}%/{r['change24h']:+.1f}%" for r in ranked) or "none"

        if self.cfg["season"]["enabled"]:
            on, detail, _ = strategy.season_gate(overview, sources.global_market())
            if not on:
                self.log_run("no season", detail)
                return

        target, reason, rotation = strategy.pick_target(ranked, n)
        rotation_note = ""
        if rotation:
            rotation_note = (f"Rotation flag: leader {ranked[0]['name']} is {ranked[0]['change7d']:+.1f}% on 7d, "
                             f"above +{n['leader_rotation_7d_pct']}%: no new entries in it")
        if not target:
            self.log_run("no target", f"{reason}. 7d/24h ranking: {ranking}. {rotation_note}".strip())
            return

        coins = strategy.screen_coins(target["slug"], self.cfg["coins"])
        g, min_liq = self.cfg["general"], self.cfg["coins"]["min_pair_liquidity_usd"]
        n_pass = n_sent = 0
        for rank, c in enumerate(coins, 1):
            try:
                chain, addr = self.contract(c["id"])
                pair = sources.dex_pair(chain, addr) if addr else None
                checks, snap = safety.check_token(chain, addr, self.cfg["safety"])
            except sources.DataError as e:
                chain = addr = pair = None
                checks, snap = [safety.Check("Safety scan", "unknown", str(e))], {}
            liq = pair["liquidity_usd"] if pair else None
            if min_liq:
                checks.insert(0, safety.Check(
                    "DEX liquidity", "unknown" if liq is None else ("pass" if liq >= min_liq else "fail"),
                    f"{money(liq)} (need {money(min_liq)})"))

            ok = safety.passed(checks, g["unknown_counts_as_fail"])
            sent = False
            if ok:
                n_pass += 1
                if not self.in_cooldown(c["id"]):
                    sent = self.email_buy(c, chain, addr, liq, target, rank, len(coins), len(ranked), checks, rotation_note)
                    if sent:
                        n_sent += 1
                        self.state.setdefault("alerted", {})[c["id"]] = iso(self.t)
                        # Kept so a position bought off this email is compared against
                        # the coin as it was at the signal, not at the bot's next run.
                        self.state.setdefault("signal_snapshots", {})[c["id"]] = {
                            "at": iso(self.t), "liquidity_usd": liq, **snap}

            failed = [f"{x.name}: {x.value}" for x in checks if not x.ok]
            print(f"  #{rank} {c['symbol'].upper():<10} {'PASS' if ok else 'FAIL'}  {'; '.join(failed)}")
            if not self.dry_run:
                store.log_signal({
                    "run_utc": iso(self.t), "mode": "PAPER" if self.paper else "LIVE",
                    "narrative": target["name"], "narrative_slug": target["slug"],
                    "narrative_7d": target["change7d"], "narrative_24h": target["change24h"],
                    "volume_rank": rank, "coingecko_id": c["id"], "symbol": c["symbol"].upper(),
                    "chain": chain or "", "contract": addr or "", "price_usd": c["price"],
                    "market_cap_usd": c.get("marketCap"), "volume_24h_usd": c["volume24h"],
                    "liquidity_usd": liq, "result": "PASS" if ok else "FAIL",
                    "failed_checks": " | ".join(failed),
                    "checks": " | ".join(f"{x.name}={x.status}:{x.value}" for x in checks),
                    "emailed": "yes" if sent else "no",
                })
        self.log_run("screened", f"{reason}; {n_pass}/{len(coins)} coins passed safety, {n_sent} emailed. "
                                 f"{rotation_note}".strip())

    def in_cooldown(self, coin_id):
        last = self.state.get("alerted", {}).get(coin_id)
        hours = self.cfg["general"]["alert_cooldown_hours"]
        return bool(last) and self.t - parse_iso(last) < timedelta(hours=hours)

    def email_buy(self, c, chain, addr, liq, target, rank, n_coins, n_narr, checks, rotation_note):
        sym = c["symbol"].upper()
        ex, n = self.cfg["exits"], self.cfg["narratives"]
        fields = {
            "Coin": f"{c['name']} ({sym})",
            "Contract": addr,
            "Chain": chain,
            "Narrative": (f"{target['name']}: 7d {target['change7d']:+.2f}%, 24h {target['change24h']:+.2f}% "
                          f"({target['label']}), ranked #{n['target_rank']} of {n_narr} by 7d return"),
            "Volume rank in narrative": f"{rank} of {n_coins}",
            "Price": money(c["price"]),
            "Market cap": money(c.get("marketCap")),
            "24h volume": money(c["volume24h"]),
            "DEX liquidity": money(liq),
        }
        for x in checks:
            if x.status == "pass":
                fields[f"Passed: {x.name}"] = x.value
        manual = [f"{x.name} ({x.value})" for x in checks if x.status == "manual"] + MANUAL_ALWAYS
        for i, m in enumerate(manual, 1):
            fields[f"Check by hand {i}"] = m
        fields["Size limit"] = self.size_text()
        fields["Exit if"] = (f"{target['name']} becomes the leader above +{n['leader_rotation_7d_pct']}% on 7d, "
                             f"or no +{ex['time_stop_min_move_pct']}% move within {ex['time_stop_hours']}h of entry")
        if rotation_note:
            fields["Rotation flag"] = rotation_note
        price = f"{c['price']:.12f}".rstrip("0").rstrip(".")
        fields["positions.csv row (if you buy, add this and fix price and size)"] = (
            f"{iso(self.t)},{sym},{c['id']},{chain},{addr},{target['slug']},{price},SIZE_USD,open")
        fields["Note"] = FOOTER + " Signal is logged for scoring."
        return self.email(f"Buy candidate: {sym} ({target['name']})", fields)

    # --- open positions ------------------------------------------------------

    def position_market(self, positions):
        """{id(p): (price, DEX liquidity)}. Price comes from CoinGecko when the row has an id,
        else from the token's biggest DexScreener pool."""
        try:
            by_id = sources.prices_by_id([p["coingecko_id"] for p in positions if p.get("coingecko_id")])
        except sources.DataError as e:
            print(f"price lookup failed: {e}", file=sys.stderr)
            by_id = {}
        out = {}
        for p in positions:
            pair = None
            if p.get("contract"):
                try:
                    pair = sources.dex_pair(p.get("chain"), p["contract"])
                except sources.DataError:
                    pass
            price = by_id.get(p.get("coingecko_id"))
            if price is None and pair:
                price = pair["price_usd"]
            out[id(p)] = (price, pair["liquidity_usd"] if pair else None)
        return out

    def holder_snapshot(self, p):
        if not p.get("contract"):
            return {}
        try:
            return safety.check_token(p.get("chain"), p["contract"], self.cfg["safety"])[1]
        except sources.DataError:
            return {}

    def entry_snapshot(self, p, st, opened, liq):
        """What a position is compared against: the buy email's snapshot when the bot emailed
        this coin in the 24h before entry, otherwise the first check after the row was added."""
        if "entry_snapshot" not in st:
            sig = self.state.get("signal_snapshots", {}).get(p.get("coingecko_id"))
            if sig and timedelta(hours=-1) <= opened - parse_iso(sig["at"]) <= timedelta(hours=24):
                st["entry_snapshot"] = {**sig, "source": "the buy email"}
            else:
                snap = st.pop("holders_at_entry", None) or self.holder_snapshot(p)
                st["entry_snapshot"] = {"at": iso(self.t), **snap, "source": "the bot's first check"}
        snap = st["entry_snapshot"]
        if snap.get("liquidity_usd") is None and liq is not None:
            snap["liquidity_usd"] = liq
        return snap

    def check_positions(self, overview, ranked):
        positions = store.open_positions()
        if not positions:
            return
        market = self.position_market(positions)
        ex, n = self.cfg["exits"], self.cfg["narratives"]
        for p in positions:
            try:
                entry, opened = float(p["entry_price_usd"]), parse_iso(p["opened_utc"])
                if entry <= 0:
                    raise ValueError
            except (KeyError, ValueError):
                print(f"skipping positions.csv row with bad price or date: {p}", file=sys.stderr)
                continue
            key = f"{p.get('coingecko_id') or p.get('contract')}@{p['opened_utc']}"
            st = self.state.setdefault("positions", {}).setdefault(key, {})
            price, liq = market[id(p)]
            hours = (self.t - opened).total_seconds() / 3600
            if price is not None and hours <= ex["time_stop_hours"]:
                st["peak"] = max(st.get("peak") or 0, price)
            base = {"Coin": p["symbol"], "Contract": p.get("contract") or "n/a",
                    "Entry price": money(entry), "Current price": money(price),
                    "Change since entry": f"{(price / entry - 1) * 100:+.1f}%" if price else "n/a",
                    "DEX liquidity now": money(liq), "Held for": f"{hours:.0f}h"}
            snap = self.entry_snapshot(p, st, opened, liq)

            # Exit: stop-loss. Price has fallen too far below entry.
            if price is not None and not st.get("stop_loss_sent") and price <= entry * (1 - ex["stop_loss_pct"] / 100):
                if self.email(f"Exit alert: {p['symbol']}, stop-loss", {
                        "Rule fired": f"price is {(price / entry - 1) * 100:+.1f}% from your entry, "
                                      f"past the -{ex['stop_loss_pct']}% stop", **base, "Note": FOOTER}):
                    st["stop_loss_sent"] = iso(self.t)

            # Exit: liquidity pulled. The main sign of a rug: the pool empties.
            was = snap.get("liquidity_usd")
            if liq is not None and was and not st.get("liquidity_sent") \
                    and liq <= was * (1 - ex["max_liquidity_drop_pct"] / 100):
                if self.email(f"Exit alert: {p['symbol']}, liquidity pulled", {
                        "Rule fired": f"DEX liquidity fell from {money(was)} to {money(liq)} "
                                      f"({(liq / was - 1) * 100:+.0f}%) since {snap['source']} ({snap['at']})",
                        **base, "Note": FOOTER}):
                    st["liquidity_sent"] = iso(self.t)

            # Exit: rotation. The position's narrative now leads, above the threshold.
            # A narrative outside the ranked groups (e.g. Pump.fun Memes, a launchpad)
            # is ranked alongside them so its positions can still trigger this.
            if ranked is not None and not st.get("rotation_sent"):
                pool = list(ranked)
                own = next((x for x in overview["narratives"] if x.get("slug") == p.get("narrative_slug")
                            and x.get("change7d") is not None), None)
                if own and all(x["slug"] != own["slug"] for x in pool):
                    pool.append(own)
                lead = max(pool, key=lambda x: x["change7d"]) if pool else None
                if lead and lead["slug"] == p.get("narrative_slug") and lead["change7d"] > n["leader_rotation_7d_pct"]:
                    if self.email(f"Exit alert: {p['symbol']}, rotation", {
                            "Rule fired": f"{lead['name']} leads at {lead['change7d']:+.1f}% on 7d, "
                                          f"above +{n['leader_rotation_7d_pct']}%", **base, "Note": FOOTER}):
                        st["rotation_sent"] = iso(self.t)

            # Exit: time stop. No +X% move within the window after entry.
            if hours >= ex["time_stop_hours"] and not st.get("time_stop_done"):
                peak = st.get("peak") or price
                if peak is not None:
                    target = entry * (1 + ex["time_stop_min_move_pct"] / 100)
                    if peak < target:
                        if self.email(f"Exit alert: {p['symbol']}, time stop", {
                                "Rule fired": f"no +{ex['time_stop_min_move_pct']}% move within "
                                              f"{ex['time_stop_hours']}h (best seen {money(peak)})",
                                **base, "Note": FOOTER}):
                            st["time_stop_done"] = iso(self.t)
                    else:
                        st["time_stop_done"] = "moved"

            if hours <= ex["monitor_hours"] and p.get("contract") and snap["at"] != iso(self.t):
                self.watch_holders(p, st, base, snap)

    def watch_holders(self, p, st, base, first):
        snap = self.holder_snapshot(p)
        if snap.get("top10_pct") is None:
            return
        if first.get("top10_pct") is None:  # the baseline scan had no holder data; start from now
            first.update(top10_pct=snap["top10_pct"], creator_balance=snap.get("creator_balance"))
            return
        ex = self.cfg["exits"]
        rise = snap["top10_pct"] - first["top10_pct"]
        if rise >= ex["max_top10_increase_pts"] and not st.get("concentration_sent"):
            if self.email(f"Holder warning: {p['symbol']}, supply concentrating", {
                    "What changed": f"top-10 holders went from {first['top10_pct']:.1f}% to "
                                    f"{snap['top10_pct']:.1f}% since {first['source']} ({first['at']})",
                    **base, "Note": FOOTER}):
                st["concentration_sent"] = iso(self.t)
        was, now = first.get("creator_balance"), snap.get("creator_balance")
        if was is not None and now is not None and now != was and not st.get("deployer_sent"):
            if self.email(f"Holder warning: {p['symbol']}, deployer moved tokens", {
                    "What changed": f"creator balance changed from {was} to {now}", **base, "Note": FOOTER}):
                st["deployer_sent"] = iso(self.t)

    # --- weekly scorecard ----------------------------------------------------

    def maybe_scorecard(self, force=False):
        sc = self.cfg["scorecard"]
        today = self.t.date().isoformat()
        due = (self.t.weekday() == sc["weekday"] and self.t.hour >= sc["hour_utc"]
               and self.state.get("last_scorecard") != today)
        if not (force or due):
            return
        since = self.t - timedelta(days=7)
        rows = [r for r in store.read_csv(store.SIGNALS)
                if r.get("emailed") == "yes" and parse_iso(r["run_utc"]) >= since]
        runs = [r for r in store.read_csv(store.RUNS) if parse_iso(r["run_utc"]) >= since]
        try:
            prices = sources.prices_by_id([r["coingecko_id"] for r in rows])
        except sources.DataError:
            prices = {}

        returns, lines = [], {}
        for r in rows:
            then, now = float(r["price_usd"]), prices.get(r["coingecko_id"])
            if now:
                ret = (now / then - 1) * 100
                returns.append(ret)
                lines[f"{r['run_utc']} {r['symbol']} ({r['narrative']})"] = f"{money(then)} → {money(now)} ({ret:+.1f}%)"
            else:
                lines[f"{r['run_utc']} {r['symbol']} ({r['narrative']})"] = f"{money(then)} → no current price"

        outcomes = Counter(r["outcome"] for r in runs)
        fields = {
            "Week ending": today,
            "Buy candidates emailed": len(rows),
            "Average return if bought at signal": f"{sum(returns) / len(returns):+.1f}%" if returns else "n/a",
            "Winners": f"{sum(1 for x in returns if x > 0)} of {len(returns)}" if returns else "n/a",
            "Hourly runs": f"{len(runs)}: " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()) if runs else "0",
            **lines,
            "Note": "Prices are CoinGecko spot; returns ignore fees, slippage and MEV. " + FOOTER,
        }
        if self.email(f"Weekly scorecard ({len(rows)} signals)", fields):
            self.state["last_scorecard"] = today

    def test_email(self):
        ok = self.email("Test: signal bot is connected", {
            "Status": "If you can read this, FormSubmit delivery works.",
            "Mode": "PAPER (signals are logged and scored, no money at risk)" if self.paper else "LIVE",
            "What you'll get": "Buy-candidate emails when a coin clears every gate, exit alerts for "
                               "positions.csv, and a scorecard every week.",
            "Note": FOOTER,
        })
        print("test email sent" if ok else "test email NOT sent (see error above)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print emails instead of sending; don't write logs")
    ap.add_argument("--test-email", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    args = ap.parse_args()

    with open(ROOT / "settings.toml", "rb") as f:
        cfg = tomllib.load(f)
    bot = Bot(cfg, dry_run=args.dry_run)
    crashed = False
    try:
        if args.test_email:
            bot.test_email()
        elif args.scorecard:
            bot.maybe_scorecard(force=True)
        else:
            bot.run()
    except Exception as e:
        # Leave a line in data/runs.csv so a failed run is visible in the repo, not only in Actions.
        traceback.print_exc()
        bot.log_run("crash", f"{type(e).__name__}: {e}")
        crashed = True
    if not args.dry_run:
        store.save_state(bot.state)
    if bot.email_errors:
        bot.log_run("email error", " | ".join(bot.email_errors))
    if crashed or bot.email_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
