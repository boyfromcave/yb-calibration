"""Differential framework on synthetic records, and the ``ybcal devnet`` CLI handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.devnet.fakenode import history_row
from ybcal import cli
from ybcal.devnet import diff as d
from ybcal.devnet.scenarios import SUITE, Schedule, make_schedule, schedule_prices
from ybcal.devnet.scrape import normalize_history_row
from ybcal.devnet.status import Skipped
from ybcal.params.paramset import ParamSet, mainnet, regtest
from ybcal.types import PricePath


def recs(n: int = 300) -> list[dict[str, Any]]:
    return [normalize_history_row(history_row(h)) for h in range(1, n + 1)]


# --- compare --------------------------------------------------------------------------------------


def test_identical_records_pass():
    rep = d.compare(recs(), recs())
    assert rep.passed and rep.compared_keys == 300 and rep.first_mismatch is None
    assert rep.summary().startswith("PASS")
    assert all(f.compared == 300 for f in rep.fields.values())


def test_mismatch_counts_and_first_height():
    sim = recs()
    for h in (150, 151, 200):
        sim[h - 1] = dict(sim[h - 1], pMint=sim[h - 1]["pMint"] + 1)
    sim[249] = dict(sim[249], haltMask=4)
    rep = d.compare(recs(), sim)
    assert not rep.passed
    pm = rep.fields["pMint"]
    assert (pm.mismatches, pm.first_mismatch, pm.first_sim - pm.first_node) == (3, 150, 1)
    assert rep.fields["haltMask"].first_mismatch == 250
    assert rep.first_mismatch == (150, "pMint")
    assert "pMint: 3/300 differ; first at height 150" in rep.summary()
    assert rep.to_dict()["fields"]["pMint"]["passed"] is False


def test_none_and_bool_are_not_integers():
    a = [{"height": 1, "pMint": None, "tagged": 1}]
    assert not d.compare(a, [{"height": 1, "pMint": 0, "tagged": 1}], ("pMint",)).passed
    assert not d.compare(a, [{"height": 1, "pMint": None, "tagged": True}], ("tagged",)).passed
    assert d.compare(a, [{"height": 1, "pMint": None, "tagged": 1}], ("pMint", "tagged")).passed


@pytest.mark.parametrize(
    "allow, passes",
    [
        (["pMint"], True),
        (["pMint@150-200"], True),
        (["pMint@150"], False),
        ([("pMint", 150, 200)], True),
        ([("pMint", 150), ("pMint", 151), ("pMint", 200)], True),
        (["pFast"], False),
        ([], False),
    ],
)
def test_allowlist(allow: list[Any], passes: bool):
    sim = recs()
    for h in (150, 151, 200):
        sim[h - 1] = dict(sim[h - 1], pMint=1)
    rep = d.compare(recs(), sim, allowlist=allow)
    assert rep.passed is passes
    assert rep.fields["pMint"].allowed + rep.fields["pMint"].mismatches == 3


def test_missing_keys_and_duplicates():
    rep = d.compare(recs(10), recs(12))
    assert rep.missing_in_node == [11, 12] and not rep.passed
    assert d.compare(recs(10), recs(12), require_same_keys=False).passed
    assert not d.compare([], []).passed  # nothing compared is not a pass
    with pytest.raises(ValueError, match="duplicate"):
        d.compare(recs(2) + recs(1), recs(2))


def test_vault_key():
    node = [{"vault": "a:0", "status": "ACTIVE"}, {"vault": "b:0", "status": "CLAIMED"}]
    sim = [{"vault": "a:0", "status": "ACTIVE"}, {"vault": "b:0", "status": "CLOSED"}]
    rep = d.compare(node, sim, ("status",), key="vault")
    assert rep.fields["status"].first_mismatch == "b:0"


# --- suite ----------------------------------------------------------------------------------------


def test_suite_pending_without_simulator():
    rep = d.validate_suite()
    assert [r.name for r in rep.results] == list(SUITE)
    assert all(r.status == "pending" and "WP-3..5" in r.reason for r in rep.results)
    assert not rep.validated and not rep.failed
    assert "NOT VALIDATED" in rep.summary()


def _echo_sim(params: ParamSet, path: PricePath, sched: Schedule) -> list[dict[str, Any]]:
    assert path.n_steps == sched.total_blocks and path.resolution == "block"
    return recs(sched.total_blocks)


def test_suite_skipped_without_nodes_and_pass_with_both():
    rep = d.validate_suite(["calm"], _echo_sim)
    assert rep.results[0].status == "skipped"
    rep = d.validate_suite(["calm", "crash-70"], _echo_sim, node_runner=lambda s: recs(s.total_blocks))
    assert rep.validated and [r.status for r in rep.results] == ["pass", "pass"]
    rep = d.validate_suite(["calm"], _echo_sim, node_runner=lambda s: Skipped("no binary", "run"))
    assert rep.results[0].status == "skipped" and "no binary" in rep.results[0].reason


def test_suite_fails_on_mismatch_and_reports_errors():
    def off_by_one(p: ParamSet, path: PricePath, s: Schedule) -> list[dict[str, Any]]:
        out = recs(s.total_blocks)
        out[-1] = dict(out[-1], supplyCents=1)
        return out

    rep = d.validate_suite(["calm"], off_by_one, node_runner=lambda s: recs(s.total_blocks))
    assert rep.failed and rep.results[0].status == "fail"
    assert rep.results[0].report.fields["supplyCents"].first_mismatch == rep.results[0].blocks

    def boom(*a: Any) -> list[dict[str, Any]]:
        raise RuntimeError("sim crashed")

    rep = d.validate_suite(["calm"], boom, node_runner=lambda s: recs(3))
    assert rep.results[0].status == "error" and "sim crashed" in rep.results[0].reason
    assert d.validate_suite(["nope"], _echo_sim).results[0].status == "error"


def test_schedule_prices_path():
    s = make_schedule("crash-70", regtest(), seed=2)
    p = schedule_prices(s)
    assert p.n_steps == s.total_blocks and p.prices[0, 0] == 0 and p.prices[0, -1] > 0


def test_resolve_simulator_absent_until_wp3():
    import ybcal.sim.engine as eng

    assert d.resolve_simulator() is (getattr(eng, "simulate_devnet", None))


# --- CLI ------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("YBCAL_YCASHD", raising=False)
    monkeypatch.setenv("YBCAL_WORK", str(tmp_path / "work"))
    return tmp_path


def test_cli_validate_pending(env: Path, capsys: pytest.CaptureFixture[str]):
    assert cli.main(["devnet", "validate", "--scenario", "calm"]) == 0
    out = capsys.readouterr().out
    # PENDING until WP-3's simulate_devnet exists, SKIPPED (no ycashd) once it does
    assert ("PENDING" in out or "SKIPPED" in out) and "NOT VALIDATED" in out
    reports = list((env / "work" / "devnet").glob("validate-*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text())["validated"] is False
    assert cli.main(["devnet", "validate", "--scenario", "calm", "--strict"]) == 3
    assert cli.main(["devnet", "validate", "--scenario", "nope"]) == 1


def test_cli_run_skipped_and_dry_run(env: Path, capsys: pytest.CaptureFixture[str]):
    assert cli.main(["devnet", "run", "--scenario", "calm"]) == 0
    assert "skipped (binary): no ycashd binary" in capsys.readouterr().out
    assert cli.main(["devnet", "run", "--scenario", "calm", "--strict"]) == 3
    capsys.readouterr()
    assert cli.main(["devnet", "run", "--scenario", "hashrate-drop", "--dry-run", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schedule"]["needs"] == ["dark_miner"] and "yellowback=1" in doc["node0_conf"]


def test_cli_run_scales_a_mainnet_overlay(env: Path, capsys: pytest.CaptureFixture[str]):
    f = env / "main.json"
    f.write_text(mainnet().to_json())
    assert cli.main(["devnet", "run", "--scenario", "calm", "--overlay", str(f), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "scaled a main set by 31.5" in out and "-yellowbacksigmaref=10000" in out


def test_cli_build_paths(env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    assert cli.main(["devnet", "build", "--ycash6", str(env / "none")]) == 0
    assert "skipped (build): no ycash6 clone" in capsys.readouterr().out
    ov = env / "ov.json"
    ov.write_text(json.dumps({"grace": 30}))
    assert cli.main(["devnet", "build", "--overlay", str(ov), "--from-ci-run", "1"]) == 4
    assert "carries the stock RegtestParams" in capsys.readouterr().err
    exe = env / "ycashd"
    exe.write_text("#!/bin/sh\necho 'Ycash Daemon version v6.21.0-rc1-94bafa4'\n")
    exe.chmod(0o755)
    code = cli.main(["devnet", "build", "--ycashd", str(exe), "--ycash6", str(env / "none")])
    out = capsys.readouterr()
    assert code == 4 and "refused" in out.out  # unknown relation without a clone: refused …
    code = cli.main(["devnet", "build", "--ycashd", str(exe), "--allow-version-skew"])
    assert code == 0 and "warning: version skew allowed" in capsys.readouterr().out  # … unless allowed


def test_cli_build_dry_run_with_clone(env: Path, capsys: pytest.CaptureFixture[str]):
    from tests.conftest import ycash6_path

    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone")
    ov = env / "ov.json"
    ov.write_text(json.dumps({"deviationBps": 1500}))
    assert (
        cli.main(["devnet", "build", "--ycash6", str(repo), "--overlay", str(ov), "--dry-run", "--json"]) == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["compiled"] == {"deviationBps": 1500} and "+    r.deviationBps = 1500;" in doc["patch"]
    assert doc["steps"][0].startswith("./zcutil/build.sh")
