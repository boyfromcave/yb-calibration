"""Hardening plan H0-b: the empty-class convention, the proposed ``mintRequiresArmed`` field, the policy
base set and the worse-window ratio lock rule (policy/harden-2026-10.toml)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.classes import disable, disabled_classes, enabled_classes
from ybcal.params.extract import Extracted, check_drift, load_snapshot
from ybcal.params.invariants import Context, check_all
from ybcal.params.paramset import ParamSet, candidate, mainnet, policy_base, regtest
from ybcal.params.registry import REGISTRY, field_names, proposed_names
from ybcal.report.robust import worse_window
from ybcal.sim import agents as AG
from ybcal.sim import vaults as VB

from .conftest import REPO_ROOT

HARDEN = REPO_ROOT / "policy" / "harden-2026-10.toml"


def _names(ps, ctx=None):
    return {v.invariant for v in check_all(ps, ctx)}


def _a_only(ps: ParamSet) -> ParamSet:
    return ps.replace({**disable(1, ps), **disable(2, ps)})


# --- empty-class convention ------------------------------------------------------------------------


def test_empty_class_convention_is_accepted():
    ps = _a_only(candidate())
    assert enabled_classes(ps) == (0,)
    assert disabled_classes(ps) == (1, 2)
    assert "class_contiguous" not in _names(ps)
    assert check_all(ps) == []


def test_disabled_middle_class_keeps_the_others_contiguous():
    ps = candidate()
    off_b = ps.replace(disable(1, ps))
    # C still starts right after B's (empty) range, which is not A's max + 1: the enabled classes
    # A and C are no longer adjacent
    assert "class_contiguous" in _names(off_b)
    ok = off_b.replace({"classMin[1]": 103_681, "classMax[1]": 103_680,
                        "classMin[2]": 103_681, "classMax[2]": 2_102_400})
    assert "class_contiguous" not in _names(ok)


def test_class_a_cannot_be_disabled():
    ps = candidate()
    assert "class_contiguous" in _names(ps.replace(disable(0, ps)))


def test_ratio_invariants_read_the_enabled_classes_only():
    ps = _a_only(candidate()).replace({"globalRatioHaltBps": 30_000, "baseRatioBps[2]": 30_000})
    assert "base_gt_halt" not in _names(ps)  # class C 300 % = halt is moot once C is off (H-11)
    assert "base_gt_halt" in _names(candidate().replace({"globalRatioHaltBps": 30_000}))


# --- proposed field ---------------------------------------------------------------------------------


def test_mint_requires_armed_is_a_proposed_field():
    spec = REGISTRY["mintRequiresArmed"]
    assert spec.proposed == "H-1" and spec.unit == "bool"
    assert spec.mainnet is False and spec.regtest is False  # the pin's behaviour
    assert "mintRequiresArmed" in proposed_names()
    assert "mintRequiresArmed" not in field_names()
    assert mainnet()["mintRequiresArmed"] is False


def test_drift_tolerates_the_absent_field_and_compares_it_once_present():
    snap = load_snapshot()
    assert check_drift(REGISTRY, snap) == []
    d = snap.to_dict()
    d["fields"].append("mintRequiresArmed")
    for net in d["networks"]:
        d["networks"][net]["mintRequiresArmed"] = net != "regtest"
    landed = Extracted.from_dict(d)
    kinds = {(x.kind, x.name, x.network) for x in check_drift(REGISTRY, landed)}
    assert ("value-mismatch", "mintRequiresArmed", "main") in kinds
    assert ParamSet.from_extracted(landed, "main")["mintRequiresArmed"] is True


def test_mint_verdict_halts_unarmed_mints():
    ps = regtest().replace(startHeight=10)
    snap = VB.Snap(height=190, x_mint=2_000_000, x_claim=2_000_000, p_fast=2_000_000, issued_zat=10**15)
    tx = VB.MintTx(0, 10_000, 190 + 60, 190, 10**12, fee_zat=10**10)
    assert VB.mint_verdict(VB.RuleParams.of(ps), 200, tx, snap, 0) == VB.OK
    on = VB.RuleParams.of(ps.replace(mintRequiresArmed=True))
    assert VB.mint_verdict(on, 200, tx, snap, 0) == VB.MINT_HALTED_UNARMED


def test_disabled_class_takes_no_demand():
    ps = _a_only(mainnet())
    a = AG.sample_mint_attempts(np.random.default_rng(1), ps, AG.AgentsConfig(), 20_000, 48)
    assert len(a) > 0 and set(np.unique(a.term_class).tolist()) == {0}


def test_scaling_keeps_disabled_classes_disabled():
    from ybcal.params.scaling import scale_to_regtest

    res = scale_to_regtest(policy_base(Policy.load(HARDEN))).params
    assert disabled_classes(res) == (1, 2)
    assert res["mintRequiresArmed"] is True


# --- the hardening policy ----------------------------------------------------------------------------


def test_harden_policy_base():
    pol = Policy.load(HARDEN)
    b = policy_base(pol)
    assert b.network == "main"
    assert disabled_classes(b) == (1, 2)
    assert b["mintRequiresArmed"] is True
    assert (b["attestArmMin"], b["feeBps"], b["attestFeeBps"], b["maxMint"]) == (7, 15, 5000, 250_000)
    assert (b["globalRatioHaltBps"], b["recapRatioBps"]) == (30_000, 60_000)
    # the October 2026 recommendation underneath
    assert (b["sigmaRefBps"], b["sigmaMultMaxBps"], b["claimThresholdBps"], b["baseRatioBps[0]"]) == (
        18_000, 47_500, 12_500, 72_500)
    assert b.check(Context.from_policy(pol, release_tip=pol.release_tip)) == []
    assert pol.ratio_lock_rule == "worse-window" and pol.ratio_lock_params == ("baseRatioBps[0]",)
    assert "baseRatioBps[0]" not in pol.owner_pinned  # decided by the worse-window rule, not pinned
    for k in ("feeBps", "attestFeeBps", "attestArmMin", "maxMint", "globalRatioHaltBps", "classMax[1]"):
        assert k in pol.owner_pinned, k


def test_default_policy_base_is_the_shipped_set():
    assert policy_base(Policy()) == mainnet()


def test_params_check_accepts_the_harden_policy(capsys):
    from ybcal.cli import main

    assert main(["params", "check", "--policy", str(HARDEN), "--release-tip", "3052055"]) == 0
    out = capsys.readouterr().out
    assert "policy base: all invariants pass" in out and "disabled classes" in out


def test_policy_rejects_unknown_base_values():
    with pytest.raises(KeyError):
        Policy(base_values={"noSuchParam": 1})
    with pytest.raises(ValueError):
        Policy(ratio_lock_rule="best-window")


# --- worse-window rule --------------------------------------------------------------------------------


def _feas(ok_from: int) -> dict[str, list[str]]:
    return {json.dumps(v): ([] if v >= ok_from else ["bad_debt_A"]) for v in range(70_000, 85_001, 2500)}


def test_worse_window_takes_the_larger_need():
    per_run = [("full_s1", _feas(72_500)), ("full_s2", _feas(75_000)),
               ("last365_s1", _feas(80_000)), ("last365_s2", _feas(77_500))]
    ww = worse_window(per_run, ["full", "full", "last365", "last365"], ["full", "last365"])
    assert ww["needs"] == {"full": 75_000, "last365": 80_000}
    assert ww["value"] == 80_000
    assert ww["violations"] == {}


def test_worse_window_is_undecidable_without_a_feasible_value():
    per_run = [("full_s1", _feas(72_500)), ("last365_s1", _feas(99_000))]
    ww = worse_window(per_run, ["full", "last365"], ["full", "last365"])
    assert ww["needs"]["last365"] is None and ww["value"] is None
    ww = worse_window(per_run[:1], ["full"], ["full", "last365"])
    assert ww["value"] is None and ww["needs"]["last365"] is None


def test_search_bounds_widen_the_g3_ratio_lattice():
    from ybcal.studies.g3_collateral import G3Study, lattice

    assert max(lattice(72_500, "baseRatioBps[0]")) == 80_000
    assert max(lattice(72_500, "baseRatioBps[0]", lo=30_000, hi=100_000, widen=True)) == 100_000
    st = G3Study().with_policy(Policy.load(HARDEN))
    assert st.bounds == {"baseRatioBps[0]": (30_000, 250_000)}
    with pytest.raises(KeyError):
        Policy(search_bounds={"baseRatioBps[0]": [90_000, 80_000]})
