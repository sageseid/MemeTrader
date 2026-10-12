# Meme Narrative Signal Bot

Alert-only bot built from the *Meme Narrative Signal Bot — Product Dossier*. Every hour it ranks memecoin
narratives, picks the rotation target, screens that narrative's top coins, runs the safety gate, and emails
any coin that clears every gate. It never trades and never holds keys. It also watches your open positions
for exit rules and sends a weekly scorecard.

Email goes through [FormSubmit](https://formsubmit.co), so no Gmail password or SMTP setup is needed.

## The gates (one hourly pass)

1. **Season:** All Memes must be gaining dominance against total crypto market cap over 24h (Sharpe + CoinGecko).
2. **Narrative ranking:** themes sorted by 7d return; any with under $100M 24h volume are dropped.
3. **Target:** the #2 narrative, only if it's up on 7d with a flat or rising 24h. A leader above +25% 7d raises a rotation flag.
4. **Coin screen:** top 5 coins in the target by 24h volume, plus DexScreener pool liquidity.
5. **Safety:** mint and freeze revoked, LP burned/locked, largest holder under 10%, top 10 under 15%, insiders, creator rug history.
   Solana uses RugCheck; EVM chains use Sharpe's rug-check. Missing data fails the coin.
6. **Alert and log:** email each passing coin (once per 24h per coin), log every candidate to `data/signals.csv`.

A gate that fails logs why to `data/runs.csv` and stops. All thresholds are in `settings.toml`.

## Setup

1. **Activate FormSubmit (once).** Open the "Activate Form" email FormSubmit sent to your inbox and click the link.
   Then confirm delivery:
   ```bash
   python3 bot.py --test-email
   ```
2. **Try a pass without sending anything:**
   ```bash
   python3 bot.py --dry-run
   ```
3. **Run it hourly on GitHub Actions (free):**
   - Create a **private** GitHub repository and push this folder to it.
   - Optional: add a repository secret `ALERT_TO` (Settings → Secrets and variables → Actions) to override the address in `settings.toml`.
   - Actions tab → *signal-bot* → *Run workflow* → mode `test-email`. If that run fails with an activation
     message, click the new FormSubmit activation email and run it again.
   - The schedule then runs every hour and commits the logs in `data/` back to the repo.

## When you buy

Each buy email includes a ready-made `positions.csv` line. Paste it into `data/positions.csv`, put in your real
entry price and size, and commit. The bot then emails you when:

- the coin's narrative becomes the leader above +25% on 7d (rotation exit),
- the coin hasn't risen 10% within 24h of entry (time stop),
- within 72h of entry, the top-10 holders' share rises 5+ points, or the deployer's balance changes.

Set `status` to `closed` when you sell.

## Paper month

`mode = "paper"` tags every email PAPER. The Monday scorecard shows each signal's price then and now.
Switch to `mode = "live"` only if a month of scorecards justifies it, and keep each position to 1% of capital.

## Not built yet (needs keys you'd sign up for)

- **Strategy 2, smart-wallet clusters:** needs a free MadeOnSol key and a free Solana Tracker key.
- **Cluster, bundle and dev-history checks via OKX:** need a free OKX Onchain OS key. Until then the email lists them under "Check by hand".

## Files

| File | What it does |
| --- | --- |
| `bot.py` | Entry point: hourly pass, exits, scorecard, test email |
| `strategy.py` | Season gate, narrative ranking, target pick, coin screen |
| `safety.py` | Safety gate checks (RugCheck for Solana, Sharpe rug-check for EVM) |
| `sources.py` | API calls with rate limiting and retries |
| `emailer.py` | FormSubmit sender |
| `store.py` | CSV logs and `data/state.json` |
| `settings.toml` | Every threshold |

Not financial advice. The rules are one practitioner's heuristics, not a proven edge.
