"""Strategy 1 gates: season, narrative ranking, target selection, coin screen."""

import sources


def season_gate(overview, glob):
    """All Memes must be gaining dominance against the whole crypto market.

    Dominance rises over 24h exactly when the meme market cap grows faster than
    total crypto market cap, so the two 24h changes are compared directly.
    Returns (season_on, detail, dominance_pct).
    """
    all_memes = next((n for n in overview["narratives"] if n.get("group") == "aggregate"), None)
    meme_24h = all_memes.get("change24h") if all_memes else None
    total_24h = glob.get("mcap_change_24h_pct")
    if meme_24h is None or total_24h is None:
        raise sources.DataError("season gate: All Memes or total-market 24h change is null")
    dominance = overview["totalMemeMcap"] / glob["total_mcap_usd"] * 100
    detail = (f"All Memes {meme_24h:+.2f}% vs total crypto {total_24h:+.2f}% over 24h; "
              f"meme dominance {dominance:.2f}%")
    return meme_24h > total_24h, detail, dominance


def label(n, s):
    """trending / late / bottomed from 7d and 24h returns; the 1h figure is ignored."""
    d7, d24, flat = n["change7d"], n["change24h"], s["flat_band_pct"]
    if d7 >= s["strong_up_7d_pct"] and abs(d24) <= flat:
        return "late"
    if d7 > 0 and d24 > flat:
        return "trending"
    if d7 < 0 and d24 > flat:
        return "bottomed"
    if d7 > 0:
        return "up, flat 24h" if abs(d24) <= flat else "up, falling 24h"
    return "down"


def rank_narratives(overview, s):
    """Narratives in the chosen groups with enough volume, sorted by 7d return.
    Narratives with null returns or volume are skipped, never treated as flat."""
    ranked = []
    for n in overview["narratives"]:
        if n.get("group") not in s["groups"]:
            continue
        if None in (n.get("change7d"), n.get("change24h"), n.get("volume24h")):
            continue
        if n["volume24h"] < s["min_volume_24h_usd"]:
            continue
        ranked.append({**n, "label": label(n, s)})
    ranked.sort(key=lambda n: n["change7d"], reverse=True)
    return ranked


def pick_target(ranked, s):
    """Returns (target or None, reason, rotation_flag)."""
    if not ranked:
        return None, "no narrative has $%.0fM+ 24h volume" % (s["min_volume_24h_usd"] / 1e6), False
    leader = ranked[0]
    rotation = leader["change7d"] > s["leader_rotation_7d_pct"]
    k = s["target_rank"]
    if len(ranked) < k:
        return None, f"only {len(ranked)} narrative(s) pass the volume filter", rotation
    t = ranked[k - 1]
    if t["change7d"] <= 0:
        return None, f"#{k} narrative {t['name']} is down on 7d ({t['change7d']:+.2f}%)", rotation
    if t["change24h"] < -s["flat_band_pct"]:
        return None, f"#{k} narrative {t['name']} is falling on 24h ({t['change24h']:+.2f}%)", rotation
    return t, f"#{k} narrative {t['name']}: 7d {t['change7d']:+.2f}%, 24h {t['change24h']:+.2f}%", rotation


def screen_coins(target_slug, s):
    """Top N coins in the narrative by 24h volume."""
    coins = sources.memecoins(target_slug).get("coins") or []
    coins = [c for c in coins if c.get("volume24h") is not None and c.get("price") is not None]
    coins.sort(key=lambda c: c["volume24h"], reverse=True)
    return coins[: s["top_n_by_volume"]]
