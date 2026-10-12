"""CSV logs and a small JSON state file, all under data/ so GitHub Actions can commit them."""

import csv
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"
SIGNALS = DATA / "signals.csv"
RUNS = DATA / "runs.csv"
POSITIONS = DATA / "positions.csv"
STATE = DATA / "state.json"

SIGNAL_FIELDS = ["run_utc", "mode", "narrative", "narrative_slug", "narrative_7d", "narrative_24h",
                 "volume_rank", "coingecko_id", "symbol", "chain", "contract", "price_usd",
                 "market_cap_usd", "volume_24h_usd", "liquidity_usd", "result", "failed_checks",
                 "checks", "emailed"]
RUN_FIELDS = ["run_utc", "mode", "outcome", "detail"]
POSITION_FIELDS = ["opened_utc", "symbol", "coingecko_id", "chain", "contract", "narrative_slug",
                   "entry_price_usd", "size_usd", "status"]


def _append(path, fields, row):
    DATA.mkdir(exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def log_signal(row):
    _append(SIGNALS, SIGNAL_FIELDS, row)


def log_run(row):
    _append(RUNS, RUN_FIELDS, row)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def open_positions():
    return [p for p in read_csv(POSITIONS) if (p.get("status") or "open").strip().lower() == "open"]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True))
