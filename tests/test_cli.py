"""CLI smoke tests for the params commands and the dispatch convention."""

from __future__ import annotations

import json

import pytest

from ybcal import cli
from ybcal.params.extract import Extracted

from .conftest import REPO_ROOT, ycash6_path


def run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_params_show(capsys):
    rc, out, _ = run(["params", "show", "--group", "G8", "--class", "derived"], capsys)
    assert rc == 0 and "qHighBps" in out and "attestMaxAge" in out and "grace" not in out


def test_params_show_json(capsys):
    rc, out, _ = run(["params", "show", "--json"], capsys)
    data = json.loads(out)
    assert rc == 0 and {d["name"] for d in data} >= {"grace", "classMin[0]", "TOKEN_VALUE"}


def test_params_extract_snapshot(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    out_file = tmp_path / "p.json"
    rc, _, _ = run(["params", "extract", "--out", str(out_file)], capsys)
    assert rc == 0
    ex = Extracted.from_dict(json.loads(out_file.read_text()))
    assert ex.networks["main"]["startHeight"] == 3_075_000


def test_params_check_snapshot(capsys, monkeypatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    rc, out, _ = run(["params", "check", "--release-tip", "3052055"], capsys)
    assert rc == 0 and "OK" in out


def test_params_check_live(capsys):
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone")
    rc, out, _ = run(["params", "check", "--ycash6", str(repo)], capsys)
    assert rc == 0, out
    rc, out, _ = run(["params", "show", "--ycash6", str(repo), "--group", "G1"], capsys)
    assert rc == 0 and "DRIFT" not in out


def test_params_check_fails_on_invariant(capsys, monkeypatch):
    # a release tip too close to startHeight breaks M14
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    rc, out, _ = run(["params", "check", "--release-tip", "3070000"], capsys)
    assert rc == 1 and "release_lead" in out


def test_params_doc_check(capsys, tmp_path):
    rc, _, _ = run(["params", "doc", "--out", str(REPO_ROOT / "docs" / "parameters.md"), "--check"], capsys)
    assert rc == 0
    stale = tmp_path / "p.md"
    stale.write_text("old")
    rc, _, _ = run(["params", "doc", "--out", str(stale), "--check"], capsys)
    assert rc == 1
    rc, _, _ = run(["params", "doc", "--out", str(stale)], capsys)
    assert rc == 0 and stale.read_text().startswith("# Yellowback parameters")


@pytest.mark.parametrize("argv", [
    ["verify"], ["study", "G3"], ["recommend", "--budget", "quick", "--synthetic"], ["sensitivity"],
    ["data", "describe", "x.csv"], ["devnet", "validate"], ["report", "open", "reports/x"],
])
def test_unimplemented_commands_exit_2(argv, capsys):
    route = cli.ROUTES.get(tuple(argv[:2])) or cli.ROUTES[(argv[0],)]
    if cli.resolve(route) is not None:
        pytest.skip(f"{route.module}.{route.func} is implemented")
    rc, _, err = run(argv, capsys)
    assert rc == cli.EXIT_NOT_IMPLEMENTED and f"not implemented yet ({route.wp})" in err


def test_every_route_has_a_parser():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    for path in cli.ROUTES:
        assert path[0] in sub.choices
    assert set(cli.ROUTES) >= {("params", "show"), ("params", "extract"), ("params", "check")}


def test_resolve_missing_module_is_none():
    assert cli.resolve(cli.Route("ybcal.nosuch.cli", "cli_x", "WP-99")) is None
    assert cli.resolve(cli.Route("ybcal.params.cli", "cli_nosuch", "WP-0")) is None
    assert cli.resolve(cli.ROUTES[("params", "show")]) is not None
