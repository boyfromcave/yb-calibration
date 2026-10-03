"""The vendored reference model: pin headers, integrity, re-vendoring (WP-1)."""

from __future__ import annotations

import ast
import shutil
import sys

import pytest

from ybcal.model import vendor

from ..conftest import ycash6_path

PY_FILES = ("reference.py", "reference_attest.py", "reference_util.py")


def test_vendored_files_intact():
    assert vendor.check_vendored() == []


def test_headers_name_source_commit_and_hash():
    for name in PY_FILES:
        fields, body = vendor.split_header((vendor.MODEL_DIR / name).read_text())
        assert fields["commit"] == vendor.VENDORED_COMMIT
        assert fields["source"].startswith(vendor.SOURCE_REPO + " qa/rpc-tests/test_framework/")
        assert len(fields["sha256"].split()[0]) == 64
        assert "Copyright (c) 2026 The Ycash developers" in body or name == "reference_util.py"


def test_reference_keeps_mit_header_and_only_import_rewrites():
    _, body = vendor.split_header((vendor.MODEL_DIR / "reference.py").read_text())
    assert body.startswith(
        "#!/usr/bin/env python3\n# Copyright (c) 2026 The Ycash developers\n# Distributed under the MIT"
    )
    assert "from . import yellowback_attest" not in body
    assert body.count("from . import reference_attest as ya") == 8


def test_reference_modules_are_stdlib_only():
    stdlib = set(sys.stdlib_module_names)
    for name in PY_FILES:
        tree = ast.parse((vendor.MODEL_DIR / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [(node.module or "").split(".")[0]]
            else:
                continue
            assert set(mods) <= stdlib, (name, mods)


def test_tampering_is_detected(tmp_path):
    for spec in vendor.SPECS:
        shutil.copy(vendor.MODEL_DIR / spec.dest, tmp_path / spec.dest)
    shutil.copy(vendor.MODEL_DIR / vendor.MANIFEST, tmp_path / vendor.MANIFEST)
    assert vendor.check_vendored(tmp_path) == []
    f = tmp_path / "reference.py"
    f.write_text(f.read_text().replace("BPS = 10_000", "BPS = 10_001", 1))
    problems = vendor.check_vendored(tmp_path)
    assert any("reference.py" in p for p in problems)
    g = tmp_path / "yellowback_golden.json"
    g.write_text(g.read_text() + " ")
    assert any("yellowback_golden.json" in p for p in vendor.check_vendored(tmp_path))


def test_rewrites_must_match_exactly():
    with pytest.raises(ValueError, match="expected 1 line"):
        vendor.apply_rewrites("a\nb\n", [("c", "d", 1)])
    assert vendor.undo_rewrites(vendor.apply_rewrites("x\na\n", [("a", "b", 1)]), [("a", "b", 1)]) == "x\na\n"


def test_revendor_reproduces_the_shipped_files(tmp_path):
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone (set YBCAL_YCASH6)")
    try:
        written = vendor.revendor(repo, vendor.VENDORED_COMMIT, tmp_path)
    except Exception as e:  # pragma: no cover - clone without the pin
        pytest.skip(f"ycash6 clone lacks the pin: {e}")
    for name in written:
        assert (tmp_path / name).read_bytes() == (vendor.MODEL_DIR / name).read_bytes(), name
    assert (tmp_path / vendor.MANIFEST).read_text() == (vendor.MODEL_DIR / vendor.MANIFEST).read_text()
    assert vendor.check_against_source(repo) == []
