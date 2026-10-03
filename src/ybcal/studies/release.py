"""Release study — startHeight, enforceUntilHeight (PLAN §5.11). Verified and derived, never tuned.

Owner: WP-7b. Method and rules: ``docs/studies/release.md``.

Inputs
------
* **Release tip** — ``env.data["release_tip"]`` (the CLI / report driver), else ``policy.release_tip``,
  else the 6.21.0-rc1 tip **3,052,055** recorded in the comment above ``MainParams()``
  (``src/yellowback/params.cpp`` @ 7702d22: "Release 6.21.0-rc1 (2026-10-02, mainnet tip 3,052,055)").
  Its date (``release_tip_date``, default 2026-10-02) anchors the calendar estimates at 75 s a block.
* **Next network upgrade** — ``env.data["next_upgrade_height"]`` or ``policy.next_upgrade_height``
  when set, else read from ycash6's
  ``src/chainparams.cpp`` at the pin (``CMainParams``: every ``nActivationHeight`` above the tip); at
  7702d22 NU5 and later are ``NO_ACTIVATION_HEIGHT``, so none is scheduled (:data:`PINNED_UPGRADES`
  records that for runs without a clone).

Rules
-----
* ``startHeight``: KEEP while ``startHeight ≥ tip + release_lead_blocks`` (M14, 16,128); otherwise the
  smallest such height rounded up to a multiple of 1,000 (D-WP7b-6).
* ``enforceUntilHeight`` = ``startHeight + BLOCKS_PER_YEAR`` (L8) of the recommended start, and never
  past the next scheduled upgrade (then the upgrade height, with a note: the set is shorter than a year).
* Reported: the latest release tip the start still clears, calendar estimates, the renewal deadline
  (W18: sunset − ``renewal_lead_blocks``), and the W19 runbook slack (sunset − renewal deadline −
  runbook) and the abandonBlocks margin over the runbook.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ybcal.params.invariants import Context, check_all
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import PINNED_COMMIT, REGISTRY, params_for_group
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env, Metrics, Recommendation, ResultTable, final_verdict
from ybcal.studies.g1_price_windows import evidence_dir, out_dir_of
from ybcal.units import BLOCKS_PER_YEAR

OWNER_WP = "WP-7b"

GROUP = "R"
#: The rc1 tip recorded in params.cpp (MainParams comment) at the pin.
RC1_TIP = 3_052_055
RC1_DATE = "2026-10-02"
BLOCK_SECONDS = 75
START_ROUND = 1_000
#: Mainnet network-upgrade activation heights at 7702d22 (chainparams.cpp:116-153);
#: None = NO_ACTIVATION_HEIGHT.
PINNED_UPGRADES: dict[str, int | None] = {
    "OVERWINTER": 347_500,
    "SAPLING": 419_200,
    "YCASH": 570_000,
    "BLOSSOM": 1_100_000,
    "HEARTWOOD": 1_100_003,
    "CANOPY": 1_100_006,
    "NU5": None,
    "NU6": None,
    "NU6_1": None,
    "NU6_2": None,
    "ZFUTURE": None,
}


def ycash6_clone() -> Path | None:
    import os

    if os.environ.get("YBCAL_NO_YCASH6"):
        return None
    for cand in (os.environ.get("YBCAL_YCASH6"), "/home/user/ycash6"):
        if cand and (Path(cand) / ".git").exists():
            return Path(cand)
    return None


def mainnet_upgrades(repo: Path | None = None, ref: str = PINNED_COMMIT) -> tuple[dict[str, int | None], str]:
    """Mainnet ``nActivationHeight`` per upgrade from ``src/chainparams.cpp`` at ``ref`` (read with
    ``git show``; never a working tree), or :data:`PINNED_UPGRADES` without a clone. Returns the
    mapping and its source."""
    repo = repo or ycash6_clone()
    if repo is None:
        return dict(PINNED_UPGRADES), f"recorded (chainparams.cpp @ {PINNED_COMMIT})"
    try:
        src = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:src/chainparams.cpp"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return dict(PINNED_UPGRADES), f"recorded (chainparams.cpp @ {PINNED_COMMIT}; clone unreadable)"
    m = re.search(r"class CMainParams.*?class CTestNetParams", src, re.S)
    body = m.group(0) if m else src
    out: dict[str, int | None] = {}
    for name, val in re.findall(
        r"vUpgrades\[Consensus::(?:UPGRADE_)?(\w+)\]\.nActivationHeight\s*=\s*([^;]+);", body
    ):
        if name in ("BASE_SPROUT", "TESTDUMMY"):
            continue
        v = val.strip()
        out[name] = None if "NO_ACTIVATION_HEIGHT" in v else int(re.sub(r"\D", "", v))
    return out, f"ycash6 {ref}:src/chainparams.cpp"


def next_upgrade(after: int, upgrades: Mapping[str, int | None]) -> tuple[str, int] | None:
    later = [(h, n) for n, h in upgrades.items() if h is not None and h > after]
    if not later:
        return None
    h, n = min(later)
    return n, h


def height_date(h: int, tip: int, tip_date: str) -> str:
    d = datetime.fromisoformat(tip_date).replace(tzinfo=UTC) + timedelta(seconds=(h - tip) * BLOCK_SECONDS)
    return d.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class ReleaseInputs:
    tip: int
    tip_source: str
    tip_date: str
    lead: int
    renewal_lead: int
    buffer: int
    upgrade: tuple[str, int] | None
    upgrade_source: str

    @classmethod
    def build(cls, env: Env) -> ReleaseInputs:
        pol = env.policy
        d = env.data if isinstance(env.data, Mapping) else {}
        if d.get("release_tip") is not None:
            tip, src = int(d["release_tip"]), "cli"
        elif getattr(pol, "release_tip", None) is not None:
            tip, src = int(pol.release_tip), "policy"
        else:
            tip, src = RC1_TIP, "params.cpp rc1 comment (default)"
        tip_date = str(d.get("release_tip_date") or getattr(pol, "release_tip_date", None) or RC1_DATE)
        up_h = d.get("next_upgrade_height", getattr(pol, "next_upgrade_height", None))
        if up_h is not None:
            up, usrc = ("supplied", int(up_h)), "cli/policy"
        else:
            ups, usrc = mainnet_upgrades()
            up = next_upgrade(tip, ups)
        return cls(
            tip,
            src,
            tip_date,
            int(pol.release_lead_blocks),
            int(pol.renewal_lead_blocks),
            int(pol.runbook_operator_buffer_blocks),
            up,
            usrc,
        )

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["upgrade"] = list(self.upgrade) if self.upgrade else None
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ReleaseInputs:
        kw = {k: d[k] for k in cls.__dataclass_fields__}
        kw["upgrade"] = tuple(kw["upgrade"]) if kw["upgrade"] else None
        return cls(**kw)


def derive(inp: ReleaseInputs, base: ParamSet) -> tuple[int, int]:
    """(start, sunset) the rules give for ``base``: keep a start that clears the lead, else the smallest
    multiple of 1,000 that does; sunset = start + 420,480, capped at the next upgrade."""
    need = inp.tip + inp.lead
    s = int(base["startHeight"])
    if s < need:
        s = -(-need // START_ROUND) * START_ROUND
    u = s + BLOCKS_PER_YEAR
    if inp.upgrade is not None and u > inp.upgrade[1]:
        u = inp.upgrade[1]
    return s, u


def release_values(ps: Mapping, inp: ReleaseInputs) -> tuple[dict[str, float], dict[str, bool]]:
    s, u = int(ps["startHeight"]), int(ps["enforceUntilHeight"])
    W = int(ps["signalWindow"])
    runbook = W + W + inp.lead + inp.buffer
    renewal = u - inp.renewal_lead
    v = {
        "zero": 0.0,
        "tip": float(inp.tip),
        "start": float(s),
        "start_min": float(inp.tip + inp.lead),
        "lead_margin": float(s - inp.tip - inp.lead),
        "latest_tip": float(s - inp.lead),
        "sunset": float(u),
        "sunset_expected": float(s + BLOCKS_PER_YEAR),
        "next_upgrade": float(inp.upgrade[1]) if inp.upgrade else float("nan"),
        "renewal_deadline": float(renewal),
        "runbook": float(runbook),
        "runbook_slack": float(u - renewal - runbook),
        "abandon_margin": float(int(ps["abandonBlocks"]) - runbook),
        "days_to_start": (s - inp.tip) * BLOCK_SECONDS / 86400,
    }
    cons = {
        "release_lead": s >= inp.tip + inp.lead,
        "sunset_year": u == s + BLOCKS_PER_YEAR or (inp.upgrade is not None and u == inp.upgrade[1]),
        "before_upgrade": inp.upgrade is None or u <= inp.upgrade[1],
        "runbook_fits": u - renewal >= runbook,
    }
    return v, cons


def dates(ps: Mapping, inp: ReleaseInputs) -> dict[str, str]:
    s, u = int(ps["startHeight"]), int(ps["enforceUntilHeight"])
    return {
        "tip": inp.tip_date,
        "latest_release_tip": height_date(s - inp.lead, inp.tip, inp.tip_date),
        "start": height_date(s, inp.tip, inp.tip_date),
        "renewal_deadline": height_date(u - inp.renewal_lead, inp.tip, inp.tip_date),
        "sunset": height_date(u, inp.tip, inp.tip_date),
    }


@dataclass
class ReleaseStudy:
    """startHeight / enforceUntilHeight: derived from the release tip, verified (PLAN §5.11)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        return [base]

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        inp = ReleaseInputs.build(env)
        v, cons = release_values(cand, inp)
        prov = "judgement" if inp.tip_source.startswith("params.cpp") else "real-data"
        return Metrics(
            v,
            "zero",
            True,
            cons,
            prov,  # type: ignore[arg-type]
            {"inputs": inp.to_dict(), "out_dir": out_dir_of(env), "budget": env.budget.name},
        )

    def decide(self, results: ResultTable, policy: Any) -> list[Recommendation]:
        cur = results.current()
        if cur is None:
            raise ValueError("release: the current set was not evaluated")
        inp = ReleaseInputs.from_dict(cur.metrics.meta["inputs"])
        base = results.base
        s, u = derive(inp, base)
        rec_set = base.replace(startHeight=s, enforceUntilHeight=u)
        v0, c0 = release_values(base, inp)
        v1, c1 = release_values(rec_set, inp)
        ctx = Context.from_policy(
            policy, release_tip=inp.tip, next_upgrade_height=inp.upgrade[1] if inp.upgrade else None
        )
        inv = [
            f"{x.invariant}: {x.message}"
            for x in check_all(rec_set, ctx)
            if x.invariant in ("release_lead", "sunset", "start_configured", "class_locktime")
        ]
        prov = cur.metrics.provenance
        up = (
            f"{inp.upgrade[0]} at {inp.upgrade[1]}"
            if inp.upgrade
            else "none scheduled (NU5 and later have NO_ACTIVATION_HEIGHT)"
        )
        common_notes = [
            f"Release tip {inp.tip:,} ({inp.tip_source}, {inp.tip_date}); next network upgrade: {up} "
            f"[{inp.upgrade_source}].",
            f"Dates at 75 s/block: {dates(rec_set, inp)}.",
            f"Renewal deadline (W18) {int(v1['renewal_deadline']):,}; W19 runbook {int(v1['runbook']):,} "
            "blocks fits "
            f"between the deadline and the sunset with {int(v1['runbook_slack']):,} blocks to spare; "
            "abandonBlocks "
            f"exceeds the runbook by {int(v1['abandon_margin']):,} blocks.",
        ] + ([f"Invariant failures at the recommended set: {inv}"] if inv else [])
        out = evidence_dir(cur.metrics.meta.get("out_dir"), GROUP)
        ev = out / "release.csv"
        with ev.open("w") as fh:
            fh.write("metric,current,recommended\n")
            for k in v0:
                if k != "zero":
                    fh.write(f"{k},{v0[k]},{v1[k]}\n")
        keys = (
            "tip",
            "start_min",
            "lead_margin",
            "latest_tip",
            "days_to_start",
            "sunset",
            "sunset_expected",
            "next_upgrade",
            "renewal_deadline",
            "runbook",
            "runbook_slack",
            "abandon_margin",
        )
        recs = []
        for p, new, rule, binding in (
            (
                "startHeight",
                s,
                START_RULE,
                f"M14: start ≥ tip {inp.tip:,} + {inp.lead:,} = {inp.tip + inp.lead:,} (margin "
                f"{int(v1['lead_margin']):,})",
            ),
            (
                "enforceUntilHeight",
                u,
                SUNSET_RULE,
                "L8: start + 420,480"
                + (
                    f", capped at the upgrade {inp.upgrade[1]:,}"
                    if inp.upgrade and u == inp.upgrade[1]
                    else "; no scheduled upgrade caps it"
                ),
            ),
        ):
            verdict = "CHANGE" if new != base[p] else "KEEP"
            recs.append(
                Recommendation(
                    param=p,
                    current=base[p],
                    recommended=new,
                    verdict=final_verdict(verdict, prov),  # type: ignore[arg-type]
                    rule=rule,
                    binding=binding,
                    metrics={
                        "primary": "lead_margin" if p == "startHeight" else "sunset",
                        "current": {k: G3.fmt(v0[k]) for k in keys},
                        "recommended": {k: G3.fmt(v1[k]) for k in keys},
                        "constraints_current": c0,
                        "constraints_recommended": c1,
                        "dates_current": dates(base, inp),
                        "dates_recommended": dates(rec_set, inp),
                        "inputs": inp.to_dict(),
                        "design_notes": [],
                    },
                    sensitivity={
                        "sentence": "Per-release value: it moves one-for-one with the release tip; every "
                        f"{inp.lead:,}-block lead is two weeks."
                    },
                    confidence="high" if not inv else "low",
                    provenance=prov,
                    evidence=[ev],
                    group=GROUP,
                    notes=list(common_notes),
                )
            )
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        m = rec.metrics
        d = m.get("dates_recommended", {})
        what = REGISTRY[rec.param].doc
        extra = [
            rec.notes[0],
            f"Calendar (75 s blocks): start ≈ {d.get('start')}, renewal deadline ≈ "
            f"{d.get('renewal_deadline')}, sunset ≈ {d.get('sunset')}; the release must be tagged by tip "
            f"{int(m['recommended']['latest_tip']):,} (≈ {d.get('latest_release_tip')}) for this start to "
            "clear the M14 lead.",
            rec.notes[2],
        ]
        km = (
            ("lead margin (blocks)", "lead_margin", "{:,.0f}"),
            ("sunset", "sunset", "{:,.0f}"),
            ("renewal deadline", "renewal_deadline", "{:,.0f}"),
            ("runbook slack", "runbook_slack", "{:,.0f}"),
        )
        return G3.compose_explanation(rec, what=what, key_metrics=km, extra=extra)


START_RULE = (
    "startHeight = the current value while it is ≥ release tip + release_lead_blocks (M14, 16,128 blocks ≈ "
    "two "
    "weeks); otherwise the smallest multiple of 1,000 that is. Derived per release, never optimised."
)
SUNSET_RULE = (
    "enforceUntilHeight = startHeight + BLOCKS_PER_YEAR (420,480, L8), never past the next scheduled "
    "network upgrade (read from chainparams.cpp at the pin). Derived, never optimised."
)


def make_study() -> ReleaseStudy:
    """The release study (``load_study("R")``)."""
    return ReleaseStudy()


__all__ = [
    "PINNED_UPGRADES",
    "RC1_TIP",
    "ReleaseInputs",
    "ReleaseStudy",
    "dates",
    "derive",
    "mainnet_upgrades",
    "make_study",
    "next_upgrade",
    "release_values",
]
