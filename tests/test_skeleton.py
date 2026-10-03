"""Every module of the PLAN §3 tree imports and names its owner WP."""

from __future__ import annotations

import importlib
import pkgutil

import ybcal


def test_all_modules_import():
    names = [m.name for m in pkgutil.walk_packages(ybcal.__path__, "ybcal.")]
    assert len(names) >= 50
    for n in names:
        importlib.import_module(n)


def test_stub_modules_name_their_owner():
    for n in ("ybcal.model.kernels", "ybcal.sim.oracle", "ybcal.studies.g8_attestation",
              "ybcal.optimize.joint", "ybcal.devnet.diff", "ybcal.report.build", "ybcal.params.scaling"):
        assert importlib.import_module(n).OWNER_WP.startswith("WP-")
