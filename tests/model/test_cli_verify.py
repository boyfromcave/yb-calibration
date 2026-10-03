"""``ybcal verify`` (WP-1)."""

from __future__ import annotations

from ybcal import cli


def test_verify_passes(capsys, monkeypatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    rc = cli.main(["verify", "--quiet", "--samples", "50"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "checks passed" in out and "FAILED" not in out


def test_verify_table_lists_sections(capsys, monkeypatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    assert cli.main(["verify", "--samples", "20"]) == 0
    out = capsys.readouterr().out
    for word in ("golden state hash", "SIGMA-1", "RED-5", "parity", "vendored files intact"):
        assert word in out


def test_revendor_needs_a_clone(capsys, monkeypatch):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    assert cli.main(["verify", "--revendor"]) == 2
    assert "--ycash6" in capsys.readouterr().err
