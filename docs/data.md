# Data: sources, models, fitting and provenance

How `ybcal` gets price and behaviour data (PLAN §4), what each synthetic model assumes, how it is
fitted, and how provenance flows into the report. What to collect is in
[data/README.md](../data/README.md); the stress scenarios are in [scenarios.md](scenarios.md).
Code: `src/ybcal/data/` (WP-2).

## 1. Units and the price path

A price path is the frozen `ybcal.types.PricePath`: `prices` is `int64` **µUSD per YEC**, shape
`(paths, n)`, at `resolution` `"block"` (75 s) or `"hour"` (48 blocks); a `0` is a feed gap.
`ybcal.data.pricepath` adds the helpers:

- `usd_to_micro` / `clamp_prices` — round to µUSD and clamp to `params.h` `PRICE_MIN`/`PRICE_MAX`
  (100 … 10⁸ µUSD); gaps stay 0.
- `resample(pp, "block"|"hour")` — hour → block holds each hourly price for its 48 blocks (or
  `method="loglinear"`); block → hour samples blocks 0, 48, 96, … so a round trip is exact.
- `log_returns` (gaps → NaN), `timestamps`, `slice_steps`, `select_paths`.
- `save`/`load` — CSV (`ts_iso,ts,price_usd` for one path; `path_<i>` µUSD columns for many) with a
  `<file>.meta.json` sidecar, or compressed `.npz` for large ensembles.

The year is the node's: `BLOCKS_PER_YEAR` = 420,480 blocks = 8,760 hours = 365 days; volatilities
are annualised on that basis.

## 2. Real data

### 2.1 Fetchers (`ybcal data fetch`, `ybcal.data.fetch`)

Ported in behaviour (not imported) from ycash6 `contrib/yellowback/yellowback_price.py` and
`attest/calibrate/pinrate.py` at `7702d22`:

| `--source` | Endpoint | Output |
|---|---|---|
| `coingecko` | `/coins/{coin}/market_chart?vs_currency=usd&days=N` (`auto`: hourly ≤ 90 days, daily beyond); `--granularity hourly`: `/market_chart/range` in ≤ 89-day chunks; `daily`: `&interval=daily` | history CSV `ts_iso,ts,price_usd,volume_24h_usd` |
| `tickers` | `/coins/{coin}/tickers?exchange_ids=safe_trade` (fields of the `coingecko_ticker` preset: `converted_last.usd`, `bid_ask_spread_percentage`, `last_traded_at`, `is_stale`, `is_anomaly`, `volume`) | appends one row per ticker |
| `nonkyc` | `https://api.nonkyc.io/api/v2/market/getbysymbol/YEC_USDT` (`lastPriceNumber`, `bestBid/AskNumber`, `lastTradeAt` ms, `volumeNumber`; USDT at par) | appends one row |

Behaviour kept from `yellowback_price.py`: redirects are refused (they would carry the API-key
header elsewhere), reply bodies are capped (1 MiB; 16 MiB for history), bare `NaN`/`Infinity`
JSON literals and non-finite numbers are refused, `x-cg-demo-api-key` carries an optional demo
key. Added for batch use: retries with exponential backoff (honouring `Retry-After` on 429/5xx),
a minimum 2.5 s spacing between calls, and a clear **network-blocked** error (exit code 3) that
points here when DNS/proxy/firewall stop the request — the ybcal development sandbox is such a
machine. Fetchers are tested on recorded replies (`tests/data/fixtures/`) with `urlopen` mocked.

Every fetch writes `<out>.provenance.json`: `source`, `urls`, `fetched_at` (UTC), `sha256` of the
CSV, ybcal version, and for appended snapshot logs a `history` of earlier fetches.

### 2.2 Importers (`ybcal data import`, `ybcal.data.loaders`)

| `--kind` | Format | Loader |
|---|---|---|
| `price` | `ts,price_usd` (`ts` = unix s, ms, or ISO 8601; `ts_iso` accepted; aliases `timestamp`/`time`/`date`, `price`/`close`) | `load_price_csv` → `PriceSeries` |
| `spreads` | exactly `spreads.py log`: `ts_iso,ts,coingecko_micro_usd,safetrade_micro_usd,nonkyc_micro_usd,errors` | `load_spreads_csv` → `SpreadsLog` |
| `hashrate` | `height,payout_key` (one row per block) | `load_pool_shares_csv` → `PoolShareLog` |
| `depth` | `ts,depth_2pct_usd,volume_24h_usd[,bid_depth_2pct_usd]` or book levels `ts,side,price_usd,size_yec` | `load_depth_csv` → `DepthSeries` |

Rules shared by every loader:

- **UTC everywhere.** Naive ISO times are read as UTC; offsets and `Z` are honoured.
- **Duplicates:** rows are sorted by time and a repeated timestamp (or height) keeps the **last**
  row, as `pinrate.py` does; the count of dropped and of *conflicting* duplicates is reported.
- **Bad rows** (unparseable time, non-positive or non-finite price) are skipped and counted.
  `spreads` follows `spreads.py read_log`: an empty, non-integer or ≤ 0 cell is "missing".
- **Gap report** (`GapReport`): expected step = modal spacing; a gap is a spacing above
  1.5 × step (3 × the 300 s interval for spreads, as `spreads.py analyze`); missing steps,
  longest gap and coverage are reported. Gaps are reported, never silently dropped.
- **Grid resampling** (`resample_to_grid`): grid point `g` takes the last observation at or before
  `g` (as-of forward fill). `filled[k]` is True when no observation lies in `(g − step, g]`; the
  mask travels in `PricePath.meta["filled"]`. `--max-ffill-hours` turns stale points into gaps (0).
  Fitting and `describe` use only observed points (§3.6), so forward-filled values never pose as
  zero returns.

## 3. Synthetic price models (`ybcal data synth`, `ybcal.data.synthetic`)

Every model has `simulate(n_paths, n_steps, dt, rng, p0) -> PricePath(provenance="synthetic")`
(`dt` in years: `DT_BLOCK` = 1/420,480 or `DT_HOUR` = 1/8,760, or the strings `"block"`/`"hour"`;
`p0` in µUSD; column 0 = `p0`), `log_returns(...)`, `fit(PricePath)` and `preset()`.

### 3.1 Presets — placeholders, not estimates

| Model | Preset | Annualised vol |
|---|---|---|
| `gbm` | μ = 0, σ = 1.20 | 120 % |
| `merton` | μ = 0, σ = 0.95, λ = 12/yr, jump ~ N(−2 %, 20 %) in log | ≈ 118 % |
| `garch` | hourly; α = 0.06, β = 0.93 (half-life ≈ 69 h), ν = 4, ω = 1.2²·Δ·(1−α−β) | 120 % (unconditional) |
| `regime` | calm σ 80 %, μ +30 %/yr, mean spell 120 d; turbulent σ 200 %, μ −150 %/yr, mean spell 30 d | ≈ 115 % |
| `bootstrap` | none — needs real returns | — |

They are tuned only to be "YEC-like" (≈ 120 % volatility, fat tails, multi-month drawdowns above
80 %); start price $0.40. Paths simulated from a preset carry `meta["calibrated"] = False` and a
placeholder note; studies tag their results `synthetic`, so recommendations built on them are
PROVISIONAL.

### 3.2 GBM

`dlog p = (μ − σ²/2) dt + σ dW`. **Fit:** moment matching — σ = sd(r)/√Δ, μ = mean(r)/Δ + σ²/2.

### 3.3 Merton jump-diffusion

GBM plus compound-Poisson jumps (λ per year, log jump ~ N(m, s²)), drift jump-compensated so
E[p_t] = p₀e^{μt}. **Fit:** (1) threshold moments — returns more than 4 robust sds (1.4826·MAD,
re-estimated once without the jumps) from the median are jumps; σ from the rest, λ = jumps/(N·Δ),
jump mean/sd from the jumps. Small jumps hide inside the diffusion, so (2) a maximum-likelihood
polish of the "at most one jump per step" mixture `(1−λΔ)·N(m, s²) + λΔ·N(m + jm, s² + js²)`
(Nelder–Mead from step 1; kept only if it improves the likelihood). Valid while λΔ ≪ 1, which
holds for hourly and daily YEC data.

### 3.4 GARCH(1,1) with Student-t innovations

`r_t = μ + √h_t z_t`, `h_t = ω + α(r_{t−1} − μ)² + βh_{t−1}`, `z` standardised t(ν).
**Fit:** maximum likelihood, L-BFGS-B on returns scaled to unit variance, three starting points,
bounds α ∈ [10⁻⁶, 0.5], β ∈ [0, 0.9998], ν ∈ [2.05, 200], α + β < 0.9999; the variance recursion
runs through `scipy.signal.lfilter`. Discrete-time: it lives at the resolution it was fitted on
(`dt_native`); see §3.7.

### 3.5 Two-state regime switch

A continuous-time Markov chain (rates q₀₁, q₁₀ per year) switches between two GBMs (state 0 calm =
smaller σ). **Fit:** Baum–Welch EM on a 2-state Gaussian HMM of per-step returns (initialised by
splitting at the 75th percentile of |r|; stops when the log-likelihood gain < 10⁻⁷ per
observation), then mapped to annual parameters: σ_s = sd_s/√Δ, μ_s = m_s/Δ + σ_s²/2,
q = −ln(1 − p_switch)/Δ.

### 3.6 Stationary block bootstrap (Politis–Romano)

Resamples the real return series in blocks of geometric length (mean `mean_block`, default one
week of native steps, capped at n/4), circularly, from uniform start points. The marginal
distribution of resampled returns equals the empirical one; dependence within blocks is kept.
**Fit:** stores the observed returns. No preset.

**Observed returns only.** `fit_returns_of(pp)` takes returns between consecutive observed points
(not gaps, not `meta["filled"]`) and keeps those at the modal spacing, so daily data on an hourly
grid fits as daily returns with Δ = 1 day.

### 3.7 Changing resolution

GBM, Merton and the regime switch are continuous-time and simulate at any Δ. GARCH and the
bootstrap are discrete-time: to simulate on a finer grid (hourly model → blocks), each native
return is split into k sub-returns by a **Brownian bridge** whose variance matches that native
step's conditional variance (native returns are reproduced exactly; each sub-return has variance
h/k); on a coarser grid native steps are summed. Only integer ratios are supported.

### 3.8 Clamping

Log prices are clipped to [log PRICE_MIN, log PRICE_MAX] before rounding to µUSD, mirroring the
node's price bounds.

## 4. Behaviour models

### 4.1 Exchange spreads (`SpreadModel`, G6/G8)

Per source i: quote = true price × exp(bias_i + e_i,t), where e is a stationary Gaussian AR(1)
(sd `sigma_bps`, persistence time `tau_seconds`) correlated across sources (`corr`). Quotes refresh
at `refresh_per_hour` (Poisson) and are held in between (staleness); outages are an alternating
renewal process (`outage_per_day` starts, Exp(`outage_mean_hours`) durations) during which the
quote is 0. `generate(true, rng)` → `SourceQuotes` (quotes, stale and outage masks, pair spreads in
MINT-10's form `|a−b|·10⁴/min(a,b)`).

**Fit from `spreads.csv`:** deviations `log(p_i / median of the row)` over rows with every source
present give bias, sd, correlation and (lag-1 autocorrelation over consecutive rows) τ; the share
of consecutive unchanged quotes gives the refresh rate; runs of missing cells give outage rate and
mean duration. Biases are identified only relative to the cross-source median. Defaults are
placeholders.

### 4.2 Pools and tags (`PoolModel`, G1/G5/G6)

Pools with hashrate shares (remainder = "other", never tags). Who mines each block is a categorical
draw (shares may vary over time). A tagging pool publishes its 15-minute TWAP (12 blocks, the
agents' `TWAP_SECONDS = 900`) `lag_blocks` old, times exp(N(bias, noise)); `frozen` pools repeat
their first quote (PIN-1 target); outages stop tagging. **Fit from `pool-shares.csv`:** shares of
the top 8 keys above 1 % (the rest folded into "other"); noise/lag/outages keep defaults.

### 4.3 Hashrate drift (`HashrateDrift`, G5)

An Ornstein–Uhlenbeck process on share logits (σ per √day, reversion per day), softmax to shares,
evaluated hourly by default. **Fit:** σ from the sd of window-to-window logit changes.

## 5. Describe (`ybcal data describe`, `ybcal.data.describe`)

Realised volatility (annualised, observed returns only), rolling volatility, skew and excess
kurtosis, the **maximum-drawdown distribution over horizons 30 d … 5 y taken over all start dates**
(strided to ≈ one start per day, at most 50 paths), the Hill tail index of each tail (k = 5 % of
the sample, ≥ 10), autocorrelation of returns and of |returns|, and gap statistics.

## 6. Provenance rules

1. Path provenance: `real` (loaded from a file), `synthetic` (a model), `scenario` (a scenario
   program, whatever its base). Model fits record `fitted_from` and `data_provenance` in `meta`.
2. A result's provenance (`real-data` / `synthetic` / `judgement`, PLAN §2.5) is `real-data` only if
   the evidence comes from real files: real paths, the block bootstrap of real returns, or a model
   **fitted** to real data. A preset is always `synthetic`. A scenario's base inherits: real only
   when `base_path`/`data` came from real files (`meta["base_provenance"]`).
3. Every real file has a hash: fetchers write `.provenance.json`; the run manifest re-hashes every
   `--data` file (`RunManifest.verify_data`).
4. Forward-filled points are flagged (`meta["filled"]`) and never treated as observations.
5. Recommendations whose evidence is only synthetic are PROVISIONAL; the lock-readiness checklist
   needs real data for every locked parameter (PLAN §7).
