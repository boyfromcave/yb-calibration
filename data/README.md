# Data for ybcal — what to gather, where to put it

`ybcal` runs end to end on **synthetic** data out of the box, but every recommendation that rests
only on synthetic data is flagged **PROVISIONAL** (PLAN §2.5), and the lock-readiness checklist
needs real YEC data (PLAN §7, milestone M7). This file says exactly which files to collect, how,
and where they go. The models and provenance rules are in [docs/data.md](../docs/data.md).

All user data lives in **`data/local/`**, which is gitignored: nothing you fetch is committed.

```bash
mkdir -p data/local
```

The ybcal development sandbox cannot reach CoinGecko or the exchanges (its egress proxy blocks
them), so run the fetch commands on a **networked machine** — your own laptop or the attestor host
is fine — and copy the CSVs *and their `.provenance.json` sidecars* into `data/local/`.

## Checklist

| # | File | Needed for | Required? | How long |
|---|---|---|---|---|
| 1 | `data/local/yec-hourly.csv` | every price-driven study (G1–G4, G7, G8 pin rate) | **yes** | ≥ 1 year of hourly YEC/USD |
| 2 | `data/local/yec-daily.csv` | long drawdown horizons (G3 class C: 5-year terms) | recommended | all history (daily) |
| 3 | `data/local/spreads.csv` | exchange-spread model (G6 judgement, G8 `divergeBpsAttest`) | **yes** | ≥ 2 weeks at 5-minute cadence |
| 4 | `data/local/pool-shares.csv` | pool count/shares (G5 activation, G6 `peerMin`) | optional | ≥ 1 month of blocks |
| 5 | `data/local/depth.csv` | liquidity (G7 `supplyCapBps`, G9 `maxMint`) | optional | ≥ 2 weeks, hourly or daily |
| 6 | `data/local/tickers.csv`, `data/local/nonkyc.csv` | venue spreads and 24 h volume (depth proxy) | optional | logged with #3 |

### 1. Hourly YEC/USD, at least one year (required)

```bash
ybcal data fetch --source coingecko --days 365 --granularity hourly --out data/local/yec-hourly.csv
```

`--granularity hourly` makes consecutive `market_chart/range` calls of ≤ 89 days (CoinGecko returns
hourly points for a 2–90-day span) and merges them; a single `--days 365` call without it returns
daily points. The free/demo CoinGecko plan serves the past 365 days; for more, pass a key with a
longer history (`--api-key KEY` or `$YBCAL_COINGECKO_API_KEY`). The command retries with backoff
and spaces calls 2.5 s apart (the public API allows about 30 calls a minute).

Any other source works if you write the same shape: a CSV with `ts,price_usd` (unix seconds,
milliseconds or ISO 8601 UTC). `pinrate.py --save-csv` output (`ts_iso,ts,price_usd`) is accepted
as is.

Past 365 days (checked 2026-10-03: the free plan answers HTTP 401, error 10012), use
CoinMarketCap's public chart API, hourly from 2020-03-25, and splice it under the CoinGecko year:

```bash
ybcal data fetch --source coinmarketcap --granularity hourly --out data/local/yec-hourly-cmc.csv
ybcal data fetch --source coingecko --days 365 --granularity hourly --out data/local/yec-hourly-cg.csv
ybcal data splice data/local/yec-hourly-cg.csv data/local/yec-hourly-cmc.csv --out data/local/yec-hourly.csv
```

`splice` prints the overlap check (median |diff|, return correlation) and writes a provenance
sidecar naming both inputs. CoinMarketCap's YEC feed has stale stretches (one price held 359 h in
2025); fits drop runs of ≥ 6 identical prices ([docs/real-data-2026-10.md](../docs/real-data-2026-10.md)).

### 2. Daily YEC/USD, all history (recommended)

CoinGecko's free plan stops at 365 days, so the long daily series is CoinMarketCap (from
2020-03-25) spliced over CoinCodex (from the 2019-07-18 fork):

```bash
ybcal data fetch --source coinmarketcap --granularity daily --out data/local/yec-daily-cmc.csv
ybcal data fetch --source coincodex --out data/local/yec-daily-coincodex.csv
ybcal data splice data/local/yec-daily-cmc.csv data/local/yec-daily-coincodex.csv --out data/local/yec-daily.csv
```

Class C vaults run up to 5 years plus grace; only a long daily history shows multi-year drawdowns.

### 3. Exchange spreads, at least two weeks (required for G6/G8)

Use ycash6's own logger so the numbers are the attestor agents' own (`contrib/yellowback/attest/
calibrate/README.md` §1):

```bash
# from a ycash6 checkout (ycash-dd/), for two weeks
python3 contrib/yellowback/attest/calibrate/spreads.py log --out spreads.csv --interval 300 --duration 14
cp spreads.csv data/local/spreads.csv
```

`ybcal` reads its columns exactly: `ts_iso,ts,coingecko_micro_usd,safetrade_micro_usd,
nonkyc_micro_usd,errors` (empty cell = source failed on that tick).

Until two weeks are logged, a **reconstructed** history in the same layout comes from hourly data:
the aggregate's hourly point for `coingecko`, each venue's last trade (close of its last hourly
candle with volume) for `safetrade` / `nonkyc`. Hourly sampling and candle closes bias it (see
docs/real-data-2026-10.md §4); label results built on it as reconstructed.

```bash
ybcal data fetch --source safetrade-candles --start 2023-01-01 --out data/local/safetrade-yec-usdt-1h.csv
ybcal data fetch --source nonkyc-candles --start 2023-01-01 --out data/local/nonkyc-yec-usdt-1h.csv
ybcal data spreads --aggregate data/local/yec-hourly-cg.csv --safetrade data/local/safetrade-yec-usdt-1h.csv \
    --nonkyc data/local/nonkyc-yec-usdt-1h.csv --out data/local/spreads-reconstructed.csv
```

### 4. Pool shares (optional)

One row per block: `height,payout_key`, where the key is any stable pool identifier (payout
address, coinbase tag, or the key `yed_listminers` reports). Scrape it from a Ycash explorer or a
node; at least a month of blocks (≈ 35,000) gives usable shares. Without a node, the Inzyght
explorer (explorer.ycash.xyz) lists each block's coinbase payout address:

```bash
ybcal data fetch --source inzyght --blocks 70000 --out data/local/pool-shares.csv   # ~15 min, 1 call / 2.5 s
```

### 5. Order-book depth (optional)

Either a summary per snapshot

```
ts,depth_2pct_usd,volume_24h_usd[,bid_depth_2pct_usd]
```

or raw order-book snapshots, which `ybcal` summarises to the USD depth within ±2 % of the mid:

```
ts,side,price_usd,size_yec[,venue]   # side = bid | ask; one row per level, rows sharing ts form a snapshot
```

With a `venue` column each venue's book is measured around its own mid and the depths are summed.
`ybcal data fetch --source orderbooks --out data/local/depth.csv` appends one snapshot of nonkyc
(YEC/USDT, YEC/BTC at nonkyc's BTC/USDT) and SafeTrade (YEC/USDT); run it every 15 minutes.

Daily USD volume (the `yec_daily_volume_p10_usd` policy key) comes from the CoinGecko history's
`volume_24h_usd` column: `ybcal data volume data/local/yec-hourly-cg.csv --days 365`.

### 6. Venue snapshots (optional)

Snapshot fetchers append one row per call; run them from cron next to the spreads logger:

```bash
*/15 * * * *  ybcal data fetch --source tickers --exchange safe_trade --out data/local/tickers.csv
*/15 * * * *  ybcal data fetch --source nonkyc --symbol YEC_USDT --out data/local/nonkyc.csv
```

## Check what you collected

```bash
ybcal data import data/local/yec-hourly.csv --kind price          # rows, duplicates, gap report
ybcal data import data/local/spreads.csv --kind spreads           # pair spreads + fitted spread model
ybcal data import data/local/pool-shares.csv --kind hashrate      # shares per pool
ybcal data import data/local/depth.csv --kind depth
ybcal data describe data/local/yec-hourly.csv                     # vol, drawdowns, tails, gaps
ybcal data synth --model garch --calibrate data/local/yec-hourly.csv --paths 1000 --years 5 \
    --out data/local/garch-5y.npz                                 # fitted synthetic ensemble
```

Then pass the files to the studies with `--data` (repeatable), e.g.
`ybcal recommend --data data/local/yec-hourly.csv --data data/local/spreads.csv`. Every run's
manifest records the sha256 of each data file; the fetchers' `.provenance.json` sidecars record
the source URL, fetch time and the same hash.
