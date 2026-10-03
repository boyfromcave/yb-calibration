"""``ybcal data import | synth | describe`` smoke tests (fetch is covered in test_fetch)."""

from __future__ import annotations

import io

import numpy as np

from ybcal import cli
from ybcal.data import pricepath

from .test_loaders import spreads_text

T = 1_699_999_200  # on an hour boundary


def run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_routes_resolve():
    for sub in ("fetch", "import", "synth", "describe"):
        assert cli.resolve(cli.ROUTES[("data", sub)]) is not None


def test_import_price_and_write_grid(tmp_path, capsys):
    f = tmp_path / "p.csv"
    f.write_text(
        "ts,price_usd\n" + "\n".join(f"{T + i * 3600},{0.4 + 0.001 * i}" for i in range(48) if i != 10) + "\n"
    )
    out = tmp_path / "grid.csv"
    rc, text, _ = run(["data", "import", str(f), "--out", str(out)], capsys)
    assert rc == 0 and "gaps (> 1.5x step): 1" in text and out.exists()
    pp = pricepath.from_csv(out)
    assert pp.n_steps == 48 and pp.provenance == "real"


def test_import_other_kinds(tmp_path, capsys):
    s = tmp_path / "spreads.csv"
    s.write_text(spreads_text([(T + i * 300, 0.40, 0.41, 0.39 + 0.001 * (i % 3)) for i in range(30)]))
    rc, text, _ = run(["data", "import", str(s), "--kind", "spreads"], capsys)
    assert rc == 0 and "coingecko/safetrade" in text and "fitted spread model" in text
    h = tmp_path / "pools.csv"
    h.write_text("height,payout_key\n" + "\n".join(f"{i},{'AB'[i % 2]}" for i in range(100)) + "\n")
    rc, text, _ = run(["data", "import", str(h), "--kind", "hashrate"], capsys)
    assert rc == 0 and "A: 50.0%" in text
    d = tmp_path / "depth.csv"
    d.write_text(f"ts,depth_2pct_usd,volume_24h_usd\n{T},5000,20000\n{T + 3600},4000,10000\n")
    rc, text, _ = run(["data", "import", str(d), "--kind", "depth"], capsys)
    assert rc == 0 and "$4,500" in text
    rc, _, err = run(["data", "import", str(d), "--kind", "spreads"], capsys)
    assert rc == 1 and "not a spreads.py log" in err


def test_synth_and_describe(tmp_path, capsys):
    out = tmp_path / "s.npz"
    rc, text, _ = run(
        [
            "data",
            "synth",
            "--model",
            "gbm",
            "--paths",
            "4",
            "--years",
            "0.2",
            "--seed",
            "3",
            "--set",
            "sigma=0.5",
            "--out",
            str(out),
        ],
        capsys,
    )
    assert rc == 0 and "PLACEHOLDER" in text
    pp = pricepath.load(out)
    assert pp.prices.shape == (4, round(0.2 * 8760) + 1) and pp.meta["seed"] == 3
    rc, text, _ = run(["data", "describe", str(out), "--horizons", "7,30"], capsys)
    assert rc == 0 and "realised vol" in text
    rc, text, _ = run(["data", "describe", str(out), "--json"], capsys)
    assert rc == 0 and '"n_paths": 4' in text
    single = tmp_path / "one.csv"
    run(
        ["data", "synth", "--model", "merton", "--paths", "1", "--years", "0.5", "--out", str(single)], capsys
    )
    rc, text, _ = run(
        ["data", "synth", "--model", "garch", "--calibrate", str(single), "--paths", "2", "--years", "0.1"],
        capsys,
    )
    assert rc == 0 and "calibrated on" in text
    rc, _, err = run(["data", "synth", "--model", "bootstrap"], capsys)
    assert rc == 1 and "real data" in err
    assert io and np  # keep imports explicit
