# Meme Narrative Signal Bot

Alert-only bot built from the *Meme Narrative Signal Bot — Product Dossier*. Every hour it ranks memecoin
narratives, picks the rotation target, screens that narrative's top coins, runs the safety gate, and emails
any coin that clears every gate. It never trades and never holds keys. It also watches your open positions
for exit rules and sends a weekly scorecard.

Email goes through [Resend](https://resend.com) (free: 100 emails a day), so no Gmail password or SMTP setup is
needed. Formspree and FormSubmit remain as fallbacks, but neither works for alerts from GitHub: Formspree silently
drops emails that contain prices, and FormSubmit blocks GitHub's servers.

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

1. **Create a Resend API key.** Sign up free at resend.com with the email you want alerts at, then API Keys →
   Create API Key (permission: Sending access). Without your own domain, Resend only delivers to that address.
2. **Add it to GitHub.** Repo Settings → Secrets and variables → Actions → New repository secret:
   name `RESEND_API_KEY`, value the key (starts with `re_`).
3. **Test it.** Actions tab → *signal-bot* → *Run workflow* → mode `test-email`.
4. **Hourly timer.** GitHub's built-in schedule skipped almost every hour, so a free cron-job.org job starts the
   workflow at :17 past each hour: `POST https://api.github.com/repos/sageseid/MemeTrader/actions/workflows/signal-bot.yml/dispatches`
   with headers `Accept: application/vnd.github+json`, `Authorization: Bearer <token>`,
   `X-GitHub-Api-Version: 2022-11-28`, and body `{"ref":"main","inputs":{"mode":"run"}}`. The token is a
   fine-grained GitHub token for this repo only, with Actions read and write; it expires 2027-10-13.
   Each run commits its logs in `data/` back to the repo.
5. **Optional, on your Mac:** `python3 bot.py --dry-run` runs a pass without sending anything.

## When you buy

Each buy email includes a ready-made `positions.csv` line. Paste it into `data/positions.csv`, put in your real
entry price and size, and push. Pushing a change to that file runs the bot straight away. Each position is
compared against the coin as it was in the buy email, or as of that first run if there was no buy email.
The bot then emails you when:

- the price falls 30% below your entry (stop-loss; not in the dossier),
- DEX pool liquidity halves since entry, the usual sign of a rug (not in the dossier),
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
