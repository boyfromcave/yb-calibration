"""Policy TOML loading and run manifests."""

from __future__ import annotations

import dataclasses

import pytest

from ybcal.config import DEFAULT_POLICY_PATH, Policy, RunManifest


def test_default_toml_equals_dataclass_defaults():
    assert DEFAULT_POLICY_PATH.exists()
    assert Policy.load() == Policy()


def test_default_toml_sets_every_field():
    import tomllib

    seen: set[str] = set()

    def walk(t):
        for k, v in t.items():
            if k in Policy.field_names():
                seen.add(k)
            elif isinstance(v, dict):
                walk(v)

    walk(tomllib.loads(DEFAULT_POLICY_PATH.read_text()))
    # Optional (None-default) fields cannot be written in TOML; they must be documented as comments.
    text = DEFAULT_POLICY_PATH.read_text()
    optional = {f.name for f in dataclasses.fields(Policy) if f.default is None}
    assert all(f"# {k} =" in text for k in optional)
    assert seen | optional == set(Policy.field_names())


def test_plan_tolerances():
    p = Policy.load()
    assert p.max_bad_debt_prob == {"A": 0.005, "B": 0.01, "C": 0.02}
    assert p.max_bad_debt(1) == 0.01
    assert (p.max_false_halt_hours_per_year, p.attack_share_min, p.attestor_uptime) == (24.0, 0.34, 0.95)
    assert (p.expected_enforcing_share, p.materiality) == (0.80, 0.20)


def test_unknown_key_rejected(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text("[general]\nmaterialty = 0.1\n")
    with pytest.raises(KeyError):
        Policy.load(f)


def test_partial_policy_overrides(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text("materiality = 0.1\n[x.max_bad_debt_prob]\nA = 0.001\nB = 0.002\nC = 0.003\n")
    p = Policy.load(f)
    assert p.materiality == 0.1 and p.max_bad_debt("C") == 0.003
    assert p.digest() != Policy().digest()


def test_manifest_roundtrip(tmp_path):
    data = tmp_path / "prices.csv"
    data.write_text("ts,price_usd\n0,0.03\n")
    m = RunManifest.create(budget="quick", seed=7, policy=Policy(), data_files=[data], command=["recommend"])
    assert not m.synthetic_only
    path = m.save(tmp_path / "out" / "manifest.json")
    again = RunManifest.load(path)
    assert again == m
    assert again.verify_data() == {str(data): True}
    data.write_text("changed")
    assert again.verify_data() == {str(data): False}
