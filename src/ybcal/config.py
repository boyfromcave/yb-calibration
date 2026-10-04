"""Risk policy and run manifests (owner: WP-0).

:class:`Policy` is the owner's risk appetite (``policy/default.toml``); its dataclass defaults equal
that file, and a test keeps them equal. :class:`RunManifest` records everything needed to reproduce
a run (PLAN §2.7).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ybcal import __version__
from ybcal.params.registry import PINNED_COMMIT

#: Repository root (``src/ybcal/config.py`` → ``../..``).
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
#: The shipped default policy file.
DEFAULT_POLICY_PATH: Path = REPO_ROOT / "policy" / "default.toml"


def _bad_debt_default() -> dict[str, float]:
    return {"A": 0.005, "B": 0.01, "C": 0.02}


def _adoption_default() -> dict[str, dict[str, float]]:
    return {
        "low": {"yed_supply_usd": 50_000.0, "mints_per_day": 2.0, "redeems_per_day": 1.0},
        "mid": {"yed_supply_usd": 500_000.0, "mints_per_day": 10.0, "redeems_per_day": 6.0},
        "high": {"yed_supply_usd": 5_000_000.0, "mints_per_day": 50.0, "redeems_per_day": 30.0},
    }


def _owner_pinned_default() -> dict[str, Any]:
    """Parameters whose value the owner fixed by decision (D-RD-INF-2), with the decision reference.
    Plan citations (workspace ``docs/plans/``): v3 §2 W20/W21, §6.2 D-R-6/D-R-11/D-R-12; proposal
    §16 D-3/D-4 (``docs/reference/yellowback-price-attestation.md``); v2 §0 revision 4 L3 and
    revision 5 L7."""
    lock = "D-R-6 (lock classes stay, reaffirmed W21)"
    return {
        "abandonBlocks": "W21 (D-R-12)",
        "grace": "D-R-6 (reaffirmed W21)",
        "classMin[0]": lock,
        "classMin[1]": lock,
        "classMin[2]": lock,
        "classMax[0]": lock,
        "classMax[1]": lock,
        "classMax[2]": lock,
        "supplyCapBps": "W20 (D-R-11)",
        "attestFeeBps": "D-3",
        "attestArmMin": "D-4",
        "attestArmDelay": "D-4",
        # L3 fixes shares of the signal window, so they are pinned as fractions of signalWindow
        "activationThreshold": {"ref": "L3", "of": "signalWindow"},
        "participationFloor": {"ref": "L3", "of": "signalWindow"},
        "enforcementFloor": {"ref": "L3", "of": "signalWindow"},
        "enforcementResume": {"ref": "L3", "of": "signalWindow"},
        "valveBlocks": "L7",
    }


@dataclass(frozen=True)
class Policy:
    """Owner tolerances. Field meanings and units are documented in ``policy/default.toml``."""

    # general
    materiality: float = 0.20
    seed: int = 20261003
    require_real_data_for_lock: bool = True
    # risk (G3)
    max_bad_debt_prob: dict[str, float] = field(default_factory=_bad_debt_default)
    class_heterogeneity_max: float = 0.50
    term_distribution: str = "uniform"
    sigma_mult_at: str = "median"
    price_drift: str = "centred"
    real_price_model: str = "bootstrap"
    claimant_min_profit_bps: int = 200
    claimant_slippage_pctl: int = 90
    # oracle (G1, G2)
    attack_share_min: float = 0.34
    max_no_price_hours: float = 6.0
    pump_overpricing_lambda: float = 0.5
    crash_lag_cvar_alpha: float = 0.95
    activation_reliability: float = 0.99
    valve_minority_trip_max: float = 0.01
    attestor_mean_outage_blocks: int = 48
    max_harmful_capture_prob: float = 0.01
    max_grief_capture_prob: float = 0.05
    max_premature_claim_prob: float = 0.01
    claim_reaction_blocks: int = 576
    registration_notice_blocks: int = 8064
    min_capture_days: int = 90
    max_newcomer_seat_days: int = 365
    ensemble_agg: str = "worst"
    dev_absence_tolerance_days: float = 0
    dust_spend_multiple: float = 3
    residual_max_share: float = 0.01
    carrier_max_share: float = 0.001
    mint_inclusion_slack_blocks: int = 10
    release_tip: int = 3052055
    release_tip_date: str = "2026-10-02"
    yec_daily_volume_p10_usd: float | None = None
    reference_price_usd: float | None = None
    next_upgrade_height: int | None = None
    liar_bias_bps: int = 2000
    min_liar_detection: float = 0.9
    max_fee0_prob: float = 0.001
    min_registered_prob: float = 0.999
    min_liar_exclusion: float = 0.99
    max_honest_exclusion: float = 0.01
    max_accuracy_sd_bps: int = 500
    max_honest_payee_spread: float = 1.5
    min_accuracy_premium: float = 0.25
    p10_daily_volume_usd: float = 25000.0
    system_ratio_alarm_bps: int = 15000
    yed_premium_bps: int = 0
    claimant_slippage_bps: int = 100
    defector_share: float = 0.0
    lost_key_prob: float = 0.0
    attack_moved_tol: float = 0.05
    pool_outage_rate_per_day: float = 0.033333333333333
    pool_outage_mean_hours: float = 4.0
    max_sigma_lag_blocks: int = 4032
    hour_kernel_tolerance_bps: float = 300.0
    sigma_ref_round_bps: int = 500
    sigma_mult_cap_pctl: int = 99
    sigma_accept_band: tuple[float, float] = (1.0, 1.5)
    # activation (G5)
    max_false_halt_hours_per_year: float = 24.0
    expected_enforcing_share: float = 0.80
    detection_drop_share: float = 0.45
    max_detection_blocks: int = 4032
    max_flaps_per_year: float = 2.0
    operator_upgrade_window_blocks: int = 2016
    orphan_rate: float = 0.005
    max_valve_false_trips_per_year: float = 1.0
    # abandonment (G4, release)
    max_false_abandon_prob: float = 0.001
    runbook_operator_buffer_blocks: int = 4608
    release_lead_blocks: int = 16128
    renewal_lead_blocks: int = 210_240
    # owner absence (G4)
    owner_absence_median_days: float = 7.0
    owner_absence_sigma: float = 1.0
    owner_absence_rate_per_year: float = 1.0
    max_owner_miss_prob: float = 0.01
    w_owner: float = 1.0
    w_debt: float = 1.0
    # judgement (G6)
    k_dev: float = 1.5
    max_not_evaluated_prob: float = 0.05
    expected_pool_count: int = 6
    max_false_penalty_rate: float = 0.01
    # fees (G6)
    max_fee_share_small: float = 0.02
    pool_min_monthly_revenue_usd: float = 50.0
    attestor_min_monthly_revenue_usd: float = 50.0
    pool_operating_cost_usd_month: float = 20.0
    bond_opportunity_cost_apr: float = 0.05
    adoption_case: str = "low"
    adoption_scenarios: dict[str, dict[str, float]] = field(default_factory=_adoption_default)
    # supply (G7)
    max_depth_fraction: float = 0.10
    max_class_closed_days: float = 90.0
    halt_recall_floor: float = 0.90
    # attestation (G8)
    attestor_uptime: float = 0.95
    attestor_outage_correlation: float = 0.10
    max_attest_unavailability: float = 0.01
    max_single_entity_weight_share: float = 0.25
    diverge_spread_multiplier: float = 3.0
    pin_low_move_fraction: float = 0.05
    max_false_pin_prob: float = 0.01
    max_false_ejection_prob: float = 0.01
    max_dead_detection_blocks: int = 32_256
    attest_relay_budget_per_block: float = 2.0
    # amounts (G9)
    worst_price_usd: float = 100.0
    dust_zat: int = 546
    reorg_attacker_share: float = 0.30
    max_reorg_prob: float = 0.001
    max_void_prob: float = 0.01
    # optimizer
    max_rounds_joint: int = 3
    insensitive_total_order: float = 0.01
    # owner decisions (D-RD-INF-2): param → decision reference; studied, never changed
    owner_pinned: dict[str, Any] = field(default_factory=_owner_pinned_default)

    def __post_init__(self) -> None:
        from ybcal.params.registry import REGISTRY

        bad = [k for k in self.owner_pinned if k not in REGISTRY or not REGISTRY[k].tunable]
        for k, v in self.owner_pinned.items():
            if isinstance(v, dict):
                of = v.get("of")
                if "ref" not in v or (of is not None and (of not in REGISTRY or not REGISTRY[of].tunable)):
                    bad.append(f"{k} ({v})")
        if bad:
            raise KeyError(f"owner_pinned: not tunable registry parameters: {', '.join(bad)}")

    # -- loading -------------------------------------------------------------------------------------
    @classmethod
    def field_names(cls) -> frozenset[str]:
        """Every policy key."""
        return frozenset(f.name for f in dataclasses.fields(cls))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Policy:
        """From a parsed TOML document: sections are flattened; a table named after a field is
        that field's value. Unknown keys raise ``KeyError`` (typos must not pass silently)."""
        names = cls.field_names()
        flat: dict[str, Any] = {}

        def walk(tbl: dict[str, Any], path: str) -> None:
            for k, v in tbl.items():
                where = f"{path}.{k}" if path else k
                if k in names:
                    if k in flat:
                        raise KeyError(f"policy key {k!r} given twice (at {where})")
                    flat[k] = v
                elif isinstance(v, dict):
                    walk(v, where)
                else:
                    raise KeyError(f"unknown policy key {where!r}")

        walk(data, "")
        if "sigma_accept_band" in flat:
            flat["sigma_accept_band"] = tuple(flat["sigma_accept_band"])
        return cls(**flat)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Policy:
        """Load a policy TOML (default: ``policy/default.toml``; built-in defaults if absent)."""
        p = Path(path) if path is not None else DEFAULT_POLICY_PATH
        if not p.exists():
            if path is None:
                return cls()
            raise FileNotFoundError(p)
        with p.open("rb") as fh:
            return cls.from_mapping(tomllib.load(fh))

    def with_overrides(self, items: Sequence[str]) -> tuple[Policy, dict[str, Any]]:
        """Apply ``KEY=VALUE`` overrides (VALUE parsed as TOML; a bare word is a string). Returns the
        new policy and the parsed overrides. Unknown keys raise ``KeyError``."""
        out: dict[str, Any] = {}
        for it in items:
            if "=" not in it:
                raise ValueError(f"--policy-set {it!r}: expected KEY=VALUE")
            k, v = (x.strip() for x in it.split("=", 1))
            if k not in self.field_names():
                raise KeyError(f"--policy-set: unknown policy key {k!r}")
            try:
                val = tomllib.loads(f"x = {v}")["x"]
            except tomllib.TOMLDecodeError:
                val = v
            if k == "sigma_accept_band" and isinstance(val, list):
                val = tuple(val)
            out[k] = val
        return (self.replace(**out) if out else self), out

    def replace(self, **kw: Any) -> Policy:
        """A copy with fields changed (for conservative / lenient presets)."""
        return dataclasses.replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        """Plain dict (tuples become lists in JSON)."""
        return asdict(self)

    def digest(self) -> str:
        """sha256 of the canonical JSON form (the manifest's policy hash)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    def max_bad_debt(self, klass: int | str) -> float:
        """Bad-debt tolerance for class index 0..2 or name A..C."""
        key = "ABC"[klass] if isinstance(klass, int) else klass
        return float(self.max_bad_debt_prob[key])


def sha256_file(path: str | Path) -> str:
    """Hex sha256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class RunManifest:
    """Everything needed to reproduce a run (PLAN §2.7, §7 ``manifest.json``)."""

    budget: str
    seed: int
    policy_hash: str
    ycash6_commit: str = PINNED_COMMIT
    ybcal_version: str = __version__
    data_hashes: dict[str, str] = field(default_factory=dict)   #: path → sha256
    policy_path: str = ""
    command: list[str] = field(default_factory=list)
    created_utc: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    synthetic_only: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, budget: str, seed: int, policy: Policy, data_files: Sequence[str | Path] = (),
               policy_path: str | Path | None = None, command: list[str] | None = None,
               ycash6_commit: str = PINNED_COMMIT) -> RunManifest:
        """Build a manifest, hashing the data files and the policy."""
        return cls(
            budget=budget, seed=seed, policy_hash=policy.digest(), ycash6_commit=ycash6_commit,
            data_hashes={str(p): sha256_file(p) for p in data_files},
            policy_path=str(policy_path or ""), command=list(command or []),
            synthetic_only=not data_files,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict."""
        return {"format": "ybcal-manifest/1", **asdict(self)}

    def save(self, path: str | Path) -> Path:
        """Write ``manifest.json``."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        return p

    @classmethod
    def load(cls, path: str | Path) -> RunManifest:
        """Read a manifest written by :meth:`save`."""
        d = json.loads(Path(path).read_text())
        if d.pop("format", None) != "ybcal-manifest/1":
            raise ValueError(f"{path} is not a ybcal-manifest/1 file")
        return cls(**d)

    def verify_data(self) -> dict[str, bool]:
        """Re-hash each data file: path → still matches."""
        return {p: Path(p).exists() and sha256_file(p) == h for p, h in self.data_hashes.items()}
