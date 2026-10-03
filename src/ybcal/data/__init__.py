"""Price, spread, pool-share and depth data; synthetic models; scenarios (PLAN §4).

Modules:

* :mod:`~ybcal.data.pricepath` — helpers around the frozen :class:`ybcal.types.PricePath`
  (units/clamp, resampling block ↔ hour, slicing, log returns, CSV/NPZ persistence);
* :mod:`~ybcal.data.loaders` — import price / ``spreads.py`` / pool-share / depth CSVs, gap reports,
  as-of resampling onto a grid with a forward-fill mask;
* :mod:`~ybcal.data.fetch` — CoinGecko ``market_chart`` and tickers, nonkyc (needs network);
* :mod:`~ybcal.data.synthetic` — GBM, Merton, GARCH-t, regime switch, block bootstrap; spread, pool
  and hashrate-drift behaviour models;
* :mod:`~ybcal.data.scenarios` — the ``scenarios/*.toml`` stress library;
* :mod:`~ybcal.data.describe` — realised vol, drawdowns, tail index, gaps, autocorrelation;
* :mod:`~ybcal.data.cli` — ``ybcal data fetch | import | synth | describe``.

Documentation: ``docs/data.md``, ``docs/scenarios.md``, ``data/README.md``.

Owner: WP-2.
"""

OWNER_WP = "WP-2"
