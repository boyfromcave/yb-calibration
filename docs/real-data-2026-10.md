# Real data, October 2026 (milestone M7)

What real data `ybcal` now has, where it came from, how good it is, what it says, and the policy
values it supports. Fetched 2026-10-03/04 (UTC) by the data agent; files live in the shared,
gitignored `yb-calibration/data/local/`, each with a `<file>.provenance.json` sidecar (source,
URLs, fetch time, sha256; spliced and derived files name their inputs and hashes). Decisions:
D-RD-D1 … D-RD-D5 in [decisions.md](decisions.md). Commands to reproduce: §12.

Ycash Yellowback (YED) is calibrated against YEC/USD. YEC trades on two venues (nonkyc.io,
SafeTrade) for about $700–3,500 a day; one pool mines half the blocks. Every number below should be
read with that in mind.

## 1. Sources

| Source | What | Coverage | Status (2026-10-03) |
|---|---|---|---|
| CoinGecko `market_chart/range` | aggregate YEC/USD + 24 h volume, hourly | 2025-10-04 → 2026-10-04 | free plan: **365 days only** (HTTP 401, error 10012, beyond) |
| CoinMarketCap `data-api/v3/cryptocurrency/historical` | aggregate OHLCV, hourly / daily | YEC 2020-03-25 →; ZEC 2016-10 → | public chart API, no key, **unofficial**; ≤ ~750 candles per call |
| CoinCodex `get_coin_history` | aggregate daily | YEC 2019-07-19 (fork) → | free; a reply is thinned to ~600 points (chunked at 120 days) |
| nonkyc.io `market/candles`, `market/orderbook` | YEC/USDT hourly candles, books (USDT, BTC pairs) | 2023-09-18 → | free |
| SafeTrade `trade/public/markets/yecusdt/k-line`, `depth` | YEC/USDT hourly candles, book | 2023-03-10 → | free; Cloudflare refuses curl and browser agents, answers `ybcal/x` and `yellowback-quote/2` |
| Inzyght explorer `api/v1/blocks` | block height → coinbase payout address | any range (DataTables paging, ≤ 200/page) | free |
| miningpoolstats `data/ycash.js` | pool table (hashrate, last block) | snapshot | free; used only to name payout addresses |
| CryptoCompare / CoinDesk `histohour` | — | — | **now needs an API key**: not used |
| CoinPaprika `ohlcv/historical` | — | — | free plan serves **one day** of history: not used |
| Xeggex, TradeOgre | — | — | no longer list YEC (CoinGecko tickers: nonkyc and SafeTrade only) |

The attestor agents read three sources (`attest.toml.sample`, `spreads.py`): CoinGecko's aggregate
(`coingecko_simple`), SafeTrade's last trade as CoinGecko reports it (`coingecko_ticker`,
`converted_last.usd`, `max_age = 3600`), and nonkyc's last trade (`nonkyc_market`,
`lastPriceNumber`, `max_age = 3600`). `spreads.py log` reads the same three without `max_age`.

## 2. Files

| File | Rows | Range (UTC) | Content |
|---|---|---|---|
| `yec-hourly.csv` | 57,182 | 2020-03-26 → 2026-10-04 05:00 | **primary hourly series**: CoinGecko from 2025-10-04 06:00, CoinMarketCap before (splice, §3) |
| `yec-hourly-cg.csv` | 8,760 | 2025-10-04 06:00 → 2026-10-04 05:00 | CoinGecko aggregate + 24 h USD volume (the attestor's aggregate) |
| `yec-hourly-cmc.csv` | 57,182 | 2020-03-26 → 2026-10-04 05:00 | CoinMarketCap hourly OHLCV (close at close time) |
| `yec-daily.csv` | 2,634 | 2019-07-20 → 2026-10-04 | **primary daily series**: CoinMarketCap from 2020-03-26, CoinCodex before |
| `yec-daily-cmc.csv` | 2,384 | 2020-03-26 → 2026-10-04 | CoinMarketCap daily OHLCV |
| `yec-daily-coincodex.csv` | 2,635 | 2019-07-20 → 2026-10-05 (last day partial) | CoinCodex daily closes |
| `nonkyc-yec-usdt-1h.csv` | 26,490 | 2023-09-18 → 2026-10-04 | nonkyc hourly candles (base volume) |
| `safetrade-yec-usdt-1h.csv` | 31,042 (5,984 with trades) | 2023-03-10 → 2026-10-03 | SafeTrade hourly candles; zero-volume candles = no trade |
| `spreads-reconstructed.csv` | 8,760 | 2025-10-04 → 2026-10-04 | `spreads.py log` layout, reconstructed hourly (§4) |
| `spreads-reconstructed-maxage1h.csv` | 8,760 | same | same with the attestor's `max_age = 3600` on the venues |
| `spreads-reconstructed-90d.csv` | 2,190 | 2026-07-06 → 2026-10-04 | last 90 days |
| `spreads-reconstructed-long.csv` | 26,685 | 2023-09-18 → 2026-10-04 | aggregate = spliced series (CoinMarketCap before 2025-10): not attestor-faithful |
| `spreads-live.csv` | growing | 2026-10-04 05:09 → | the real `spreads.py log`, every 300 s for 14 days (§11) |
| `depth.csv` | growing (730 rows per snapshot) | 2026-10-04 05:18 → | order books `ts_iso,ts,venue,side,price_usd,size_yec`, every 15 min (§11) |
| `pool-shares.csv` | 70,000 | heights 2,983,806 → 3,053,805 (2026-08-04 → 2026-10-04) | `height,payout_key,time` |
| `pool-names.csv`, `miningpoolstats-ycash.json` | 5 / snapshot | 2026-10-04 | payout address → pool name |
| `zec-daily.csv`, `zec-hourly.csv`, `zec-hourly-cg.csv` | 3,563 / 84,784 / 8,760 | 2017-01-02 → (CG: last 365 d) | **ZEC proxy, not YEC** (CoinMarketCap; CoinGecko 365 d) |
| `zec-daily-cmc.csv`, `zec-hourly-cmc.csv` | 3,627 / 87,038 | 2016-10-29 → | ZEC including the launch weeks (distorted, prefer the 2017 files) |

## 3. Splices and cross-checks

| Pair (common timestamps) | n | median \|diff\| | p95 \|diff\| | return corr. |
|---|---|---|---|---|
| CoinGecko vs CoinMarketCap, hourly (last 365 d) | 8,760 | 68 bps | 687 bps | 0.728 |
| CoinGecko 00:00 vs CoinMarketCap daily | 365 | 57 bps | 651 bps | 0.888 |
| CoinGecko 00:00 vs CoinCodex daily | 365 | 151 bps | 1,522 bps | 0.820 |
| CoinMarketCap daily vs CoinCodex daily (2020-03 →) | 2,384 | 387 bps | 2,913 bps | 0.425 |
| CoinGecko vs nonkyc candle close, hourly | 8,624 | 111 bps | 789 bps | 0.556 |
| CoinMarketCap vs nonkyc, hourly (2023-09 →) | 26,489 | 432 bps | 2,734 bps | 0.206 |

Levels agree (median ratios 0.998–1.001); hourly *returns* agree poorly, because each aggregator
adds its own hour-to-hour noise (§5). The hourly splice is at 2025-10-04 06:00 (step return +438
bps; one hour, inside the series' own noise). The daily splice is at 2020-03-26 (step −15 bps).
CoinCodex is used only for the 250 days before CoinMarketCap's listing; its correlation with
CoinMarketCap (0.43) is the weakest link, so drawdowns starting in 2019–2020 rest on one source.

## 4. Spreads (reconstructed)

`spreads-reconstructed.csv` is built (`ybcal data spreads`) on CoinGecko's hourly timestamps:
`coingecko` = CoinGecko's hourly point; `safetrade` / `nonkyc` = the close of the venue's last
hourly candle with volume > 0 at or before that time — the venue's last trade, which is what
`converted_last` and `lastPriceNumber` return. Pairwise spread = |a − b|·10⁴/min(a, b) (MINT-10).

| Pair | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|
| coingecko / safetrade (365 d) | 272 | 1,127 | 1,540 | 3,064 | 14,555 |
| coingecko / nonkyc (365 d) | 112 | 512 | 816 | 2,267 | 9,365 |
| safetrade / nonkyc (365 d) | 306 | 1,307 | **1,906** | 3,850 | 15,061 |
| same, attestor `max_age` 1 h (n = 4,767) | 290 | 1,313 | 1,919 | 3,979 | 15,061 |
| safetrade / nonkyc, last 90 d | 242 | — | 1,301 | 2,734 | 6,233 |
| coingecko / nonkyc, last 90 d | 59 | — | 567 | 1,302 | 5,707 |

`spreads.py analyze` on these files: worst-pair p95 1,906 bps → **DIVERGE_BPS_ATTEST ≈ 3 × p95 =
5,800 bps** (365 d), **4,000 bps** (last 90 d), against the proposal's provisional 1,500. With the
attestor's `max_age = 3600`, SafeTrade is stale (no trade within the hour) in 3,929 of 8,760
hours (45 %), nonkyc in 136 (1.6 %). The fitted spread model (365 d): bias coingecko +40,
safetrade −120, nonkyc −19 bps; sd 235 / 636 / 376 bps; persistence τ ≈ 1.8 h.

Biases of the reconstruction: (1) hourly, not 5-minute sampling — fewer points, but the spread at
a random instant has the same distribution if the hour boundary is not special; (2) CoinGecko's
hourly point is a smoothed aggregate that includes both venues, which pulls `coingecko` towards
them (understates coingecko/venue spreads); (3) a candle close is the hour's *last* trade, so the
venue quote is up to an hour fresher than a live sample taken mid-hour would be (understates
staleness slightly); (4) live `spreads.py` would also see moments where CoinGecko itself failed.
The live log (`spreads-live.csv`) replaces this once it holds two weeks; first rows agree in kind
(coingecko 0.3609, safetrade 0.3532, nonkyc 0.3646: safetrade/nonkyc 322 bps).

## 5. Price statistics

From `ybcal data describe` (observed returns only; horizons in days).

| Series | Realised vol (1 h returns) | vol at 24 h returns | vol at 168 h | excess kurtosis | Hill α lower / upper |
|---|---|---|---|---|---|
| CoinGecko hourly, 365 d | 460 % | 275 % | 250 % | 48 | 1.90 / 1.87 |
| spliced hourly, 2020-03 → | 440 % | 235 % | 168 % | 54 | 1.93 / 1.99 |
| daily, 2019-07 → | — | 235 % | 166 % | 11.2 | 2.88 / 3.10 |
| nonkyc closes, 2023-09 → | 252 % | 204 % | 185 % | 169 | 1.63 / 1.53 |
| ZEC daily 2017 → (proxy) | — | 115 % | 120 % | 7.3 | 3.27 / 3.32 |

Trailing windows (spliced hourly; 1 h / 24 h return horizon): 30 d 448 % / 221 %; 90 d 386 % /
284 %; 180 d 319 % / 249 %; 365 d 461 % / 275 %; 2 y 393 % / 253 %; 3 y 367 % / 233 %; 6 y
453 % / 237 %.

**Volatility depends on the return horizon.** Hourly returns have lag-1 autocorrelation −0.19
(CoinGecko) to −0.21 (spliced); daily −0.30. Hour-to-hour aggregator noise and bid-ask bounce
roughly double the hourly variance relative to what accumulates over a day, and returns keep
mean-reverting out to a week (168 h vol 167 % on the long series). A σ estimated from hourly YEC
returns overstates multi-day risk by ~1.7×; one from daily returns overstates multi-week risk by
~1.4×. ZEC shows no such effect (115 % → 120 %).

Maximum drawdown within a window, over all start dates:

| Window | spliced hourly 2020-03 → p50 / p95 / p99 / max | daily 2019-07 → p50 / p95 / p99 / max | CoinGecko 365 d p50 / p95 / p99 |
|---|---|---|---|
| 1 day | 8.2 / 29.4 / 41.6 / 63.7 % | 0.2 / 16.2 / 26.1 / 45.4 % | 7.7 / 34.7 / 46.6 % |
| 7 days | 25.5 / 50.0 / 65.0 / 77.5 % | 15.1 / 39.2 / 48.1 / 71.5 % | 31.5 / 50.3 / 65.5 % |
| 30 days | 46.4 / 69.9 / 77.7 / 81.4 % | 36.1 / 64.6 / 71.5 / 80.5 % | 51.7 / 69.7 / 75.2 % |
| 90 days | 62.1 / 85.7 / 89.2 / 90.7 % | 57.5 / 82.6 / 92.6 / 95.5 % | 75.6 / 85.3 / 87.1 % |
| 365 days | 84.9 / 95.0 / 95.0 / 95.0 % | 79.0 / 94.3 / 97.8 / 98.8 % | — |
| 2 years | 91.8 / 96.6 / 96.6 / 96.6 % | 89.6 / 96.2 / 98.4 / 99.0 % | — |
| 5 years | 97.8 (559 starts) | 97.6 / 98.1 / 99.2 / 99.5 % | — |

The hourly 1-day figures include one-hour round-trip spikes (below); the daily column is the
cleaner short-horizon measure. YEC fell from $4.40 (fork week) to $0.019 (2024 low): −99.6 %.
The last year ran $0.052 → $1.108 → $0.36 (−92 % peak to trough inside the year, ×6 in two
months twice). Price levels: last $0.361, 7-day median $0.384, 30-day $0.557, 90-day $0.170,
365-day $0.271.

## 6. Data quality

**Flat stretches.** CoinGecko's hourly aggregate is never flat for long (13 runs of 2–3 h; 0.2 %
zero returns). CoinMarketCap's is: 2.4 % zero returns overall, five runs ≥ 6 h, the longest
**359 h (2025)** and 132 h (2024) — a stale feed, not a market. Venue closes are flat because
nothing traded: nonkyc 28.5 % zero returns (42 % in 2023, 15 % in 2026; runs ≤ 13 h); SafeTrade
traded in 5.7 % of hours in 2023, 2.6 % in 2024, 22 % in 2025, 51 % in 2026 (55 % of the last
365 days; longest gap 25 h, 123 gaps over 6 h). Effect: a GARCH likelihood collapses on long zero
runs (ω ≈ 2·10⁻¹¹, realised vol 0.1 %), and medians over a venue's last trades repeat stale
values. Fits now drop runs of ≥ 6 zero returns and the return closing each run (D-RD-D4).

**Gaps.** CoinGecko 365 d: none. CoinMarketCap hourly: 11 gaps, ≤ 3 h. nonkyc: 69 gaps (no
trades), ≤ 24 h. SafeTrade candles: 7 gaps, longest 129 h. Daily series: none.

**Outliers and wicks.** CoinGecko hourly has 16 one-hour round trips larger than 20 % (e.g.
2025-12-28 09:00 +61.7 % then −62.0 %): aggregator prints, not trades. CoinMarketCap: 146. Hourly
candle wicks (max of high/close − 1, 1 − low/close), last year: nonkyc p50 1.2 %, p95 7.7 %, p99
18 %, max 106 %; SafeTrade (traded hours) p95 20 %, p99 35 %, max 169 %.

**Venue disagreement.** §4: safetrade/nonkyc p95 19 % over the year, 13 % over the last 90 days.
SafeTrade's bid-ask spread is ~500 bps (CoinGecko `bid_ask_spread_percentage` 5.1 %), nonkyc's
~50 bps.

**Volume.** CoinMarketCap's YEC volume is unusable (zero or cents for long stretches, absent after
2026-07-29); CoinGecko's is the volume source.

**Price bounds.** The node's µUSD bounds ($0.0001–$100) clamp ZEC (above $100 for most of its
history): `ybcal data describe/synth --scale 0.001` rescales it; describe now warns when prices
were clamped.

## 7. Volume and depth

Daily 24 h USD volume (CoinGecko, the last reading of each UTC day, 2025-10-04 → 2026-10-04,
366 days): **p10 $718**, p25 $1,425, p50 $3,532, mean $7,206, min $171. Last 90 days: p10 $501,
p50 $1,712. By quarter, p10: 2025Q4 $2,083, 2026Q1 $861, 2026Q2 $923, 2026Q3 $510. Venue-implied
(nonkyc + SafeTrade YEC/USDT candle volume × close, 365 d): p10 $401, p50 $1,340 (nonkyc p50
$895, SafeTrade p50 $454) — CoinGecko also counts nonkyc's BTC and USDC pairs.

Order books (first snapshot, 2026-10-04 05:19 UTC), USD within ±2 % / ±5 % / ±10 % of each venue's
own mid:

| Venue | mid | spread | ±2 % bid / ask | ±5 % bid / ask | ±10 % bid / ask |
|---|---|---|---|---|---|
| nonkyc YEC/USDT | 0.3652 | 50 bps | $112 / $29 | $565 / $50 | $808 / $132 |
| nonkyc YEC/BTC | 0.3650 | 15 bps | $14 / $14 | $33 / $23 | $56 / $53 |
| SafeTrade YEC/USDT | 0.3632 | 523 bps | $0 / $0 | $1,340 / $37 | $1,340 / $37 |

**Total ±2 % depth: $169** (bid $126). The snapshotter (§11) accumulates the distribution; one
snapshot is not a statistic. CoinGecko's `cost_to_move_up/down_usd` (its ±2 % depth) agreed: nonkyc
$29 / $112, SafeTrade $0 / $0.

## 8. Pool shares

70,000 blocks, heights 2,983,806 → 3,053,805 (2026-08-04 → 2026-10-04; mean interval 75.4 s), no
missing heights; 31 payout addresses.

| Payout address | Pool | Share (70k) | daily share mean ± sd (min–max) |
|---|---|---|---|
| s1NYxEmfsa4Q… | **ninjaraider.com** | **52.0 %** | 52.1 ± 4.9 % (39–62 %) |
| s1jrMEF9bcZS… | unidentified (no listed pool) | 21.3 % | 21.6 ± 8.7 % (0–35 %) |
| s1iDNEgZGZLX… | mining-dutch.nl | 13.7 % | 13.7 ± 3.1 % (10–26 %) |
| s1TbPq7Mi1dN… | dapool.io | 6.1 % | 6.1 ± 1.5 % (4–9 %) |
| s1Xyu3YW4kqA… | zpool.ca (multi-coin auto-switching) | 3.7 % | 3.4 ± 8.9 % (0–29 %) |
| s1YavLWftkJY… | unidentified | 3.0 % | 3.0 ± 2.3 % (0–10 %) |
| 25 others | (swgroupe.fr and solo miners) | 0.2 % | — |

Concentration: **top-1 52.0 %, top-3 87.1 %, HHI 0.341** (effective number of pools 2.9); first
35k blocks top-1 51.0 % / top-3 90.0 % / HHI 0.347; last 35k 53.0 % / 84.1 % / 0.340. Payout keys
≥ 1 %: 6; ≥ 5 %: 4 (per day: median 4, minimum 3). Daily top-1 share p50 53 %, p90 57 %, max
62 %. miningpoolstats at fetch time: network 44.5 kSol/s; ninjaraider 25.9k (58 %), zpool 17.9k,
mining-dutch 6.4k, dapool 3.1k. Names come from matching each pool's last-block height to the
explorer's payout address (`pool-names.csv`). Fitted `HashrateDrift`: σ 0.45 per √day.

**One pool mines a majority.** Any coalition threshold below 52 % (`attack_share_min = 0.34`) is
already exceeded by one operator, and activation thresholds are decided by ninjaraider alone:
without it, at most 48 % of hash could enforce.

## 9. Synthetic models fitted to the real data

`ybcal data synth --model M --calibrate FILE --paths 200 --years 5 --p0-usd 0.36` (zero drift,
D-RD-D1):

| Model | on CoinGecko 365 d hourly | on spliced hourly | on daily 2019 → |
|---|---|---|---|
| bootstrap (demeaned) | vol 431 %, median DD 99.9 % | vol 438 % | **vol 233 %**, block 7 d |
| regime | calm σ 51 %, turbulent σ 841 % (hourly noise state) | calm 34 %, turbulent 773 % | calm 80 %, turbulent 349 %, spells ≈ 6 d / 5 d |
| merton | σ 48 %, λ 2,663/yr | σ 34 %, λ 2,820/yr | σ 87 %, λ 135/yr, jump sd 19 % |
| gbm | σ 461 % | σ 444 % | σ 236 % |
| garch | **at bounds** (α 0.5, α+β 0.9994, ν 2.3) | at bounds | at bounds (α 0.5, ν 3.0) |

GARCH(1,1)-t does not fit YEC: every fit sits on its bounds and the unconditional variance it
implies is meaningless (`data synth` now warns). The hourly fits of every parametric model read
the hourly noise as volatility (turbulent states at 800 % are noise regimes). **Use the demeaned
block bootstrap**: on the daily series for multi-day/collateral horizons (σ ≈ 233 %, the daily
mean reversion kept within 7-day blocks), on the hourly series only where the oracle's hourly
behaviour matters, knowing it carries the noise. The regime model fitted on daily data (calm 80 %,
turbulent 349 %) is a usable parametric alternative.

ZEC (2017 →, daily): vol 115 %, Hill α 3.3, 1-year max drawdown p95 87.5 %. Its daily returns
correlate with YEC's at only 0.15–0.18 (1, 3 and 6 years): ZEC is a more liquid market with
milder tails, **not** a stand-in for YEC tails; use it at most as a lower bound on tail heaviness.

## 10. Recommended values for the data-informed policy keys

Set in `policy/real-data-2026-10.toml` (= `policy/default.toml` plus these). Decision D-RD-D5.

| Key | Default | Recommended | Reasoning |
|---|---|---|---|
| `yec_daily_volume_p10_usd` | unset (150,000 in the comment) | **700** | CoinGecko 365-day p10 $718, rounded down; last 90 d $501 and venue-only $401 say not higher. Run sensitivity at 400. |
| `p10_daily_volume_usd` (G7 placeholder) | 25,000 | **700** | same measurement; the placeholder overstated liquidity 35×. |
| `expected_pool_count` | 6 | **4** | 4 payout keys ≥ 5 % (median per day; minimum 3), 6 ≥ 1 %, effective number 2.9. 6 counts two keys under 4 % that come and go (zpool hops: 0–29 % per day). |
| `expected_enforcing_share` | 0.80 | **0.70** | ninjaraider + mining-dutch + dapool = 71.8 % — the three identified, reachable operators. 0.80 needs the unidentified 21 % key too. Without ninjaraider the ceiling is 48 %: G5 should also be read at 0.48. |
| `diverge_spread_multiplier` | 3.0 | 3.0 (unchanged) | the multiplier is a judgement; the data move the *input*: worst-pair p95 1,906 bps → `divergeBpsAttest` ≈ 5,800 bps (365 d) / 4,000 (90 d) vs the provisional 1,500. Provisional until `spreads-live.csv` has two weeks. |
| `reference_price_usd` | unset (from data) | unset | from-data = last price $0.361; the 365-day median is $0.271, the 30-day $0.557: state which one a fee judgement used. |
| `worst_price_usd` | 100 | 100 (unchanged) | all-time high $5.78; $100 is 17× that, still the right ceiling for "fits a minMint vault". |
| `attack_share_min` | 0.34 | not data-set | a security goal, not a measurement — but the measured top-1 share is 0.52, so the goal is already unattainable against that one operator. Flag for the owner. |
| `orphan_rate`, `pool_outage_*`, `attestor_*` | — | unchanged | not measurable from these sources. |

What the data do not settle: the spread distribution at 5-minute cadence (live log running);
depth beyond one snapshot (snapshotter running); whether the unidentified 21 % key is a pool, a
farm or several solo miners.

## 11. Background loggers (left running)

| Logger | PID file | Output | Cadence / end |
|---|---|---|---|
| `spreads.py log` (ycash6 `contrib/yellowback/attest/calibrate/spreads.py`, read-only use) | `data/local/spreads-live.pid` | `spreads-live.csv`, log `spreads-live.log` | 300 s, stops itself after 14 days (2026-10-18 05:09 UTC) |
| order-book snapshotter `data/local/depth-snapshot-loop.sh` | `data/local/depth-loop.pid` | appends to `depth.csv`, log `depth-loop.log` | 900 s, stops itself after 14 days (≈ 2026-10-18 05:21 UTC) |

Stop: `kill $(cat data/local/spreads-live.pid) $(cat data/local/depth-loop.pid)`. Both run the
data agent's worktree venv (`wt/ybcal-data/.venv`); if that worktree is removed, repoint
`depth-snapshot-loop.sh` at `yb-calibration/.venv/bin/ybcal` (after the merge) and restart.

## 12. Reproduce

```bash
D=data/local
ybcal data fetch --source coingecko --days 365 --granularity hourly --out $D/yec-hourly-cg.csv
ybcal data fetch --source coinmarketcap --granularity hourly --out $D/yec-hourly-cmc.csv
ybcal data fetch --source coinmarketcap --granularity daily --out $D/yec-daily-cmc.csv
ybcal data fetch --source coincodex --out $D/yec-daily-coincodex.csv
ybcal data splice $D/yec-hourly-cg.csv $D/yec-hourly-cmc.csv --out $D/yec-hourly.csv
ybcal data splice $D/yec-daily-cmc.csv $D/yec-daily-coincodex.csv --out $D/yec-daily.csv
ybcal data fetch --source nonkyc-candles --start 2023-01-01 --out $D/nonkyc-yec-usdt-1h.csv
ybcal data fetch --source safetrade-candles --start 2023-01-01 --out $D/safetrade-yec-usdt-1h.csv
ybcal data spreads --aggregate $D/yec-hourly-cg.csv --safetrade $D/safetrade-yec-usdt-1h.csv \
    --nonkyc $D/nonkyc-yec-usdt-1h.csv --out $D/spreads-reconstructed.csv        # + --max-age-hours 0.9997
ybcal data fetch --source orderbooks --out $D/depth.csv
ybcal data fetch --source inzyght --blocks 70000 --out $D/pool-shares.csv      # ~15 min
ybcal data fetch --source coinmarketcap --coin zcash --granularity daily --start 2017-01-01 --out $D/zec-daily.csv
ybcal data volume $D/yec-hourly-cg.csv --days 365
ybcal data describe $D/yec-daily.csv --horizons 1,7,30,90,365,730,1825
ybcal data describe $D/zec-daily.csv --scale 0.001
ybcal data import $D/pool-shares.csv --kind hashrate
ybcal data synth --model bootstrap --calibrate $D/yec-daily.csv --paths 1000 --years 5
```

Fetched values move with every rerun (CoinGecko revises recent points; the books change each
minute). The sha256 in each sidecar identifies the bytes used here.
