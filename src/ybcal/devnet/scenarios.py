"""Regtest-scale replay schedules: the price path and behaviour a devnet run and the simulator share.

Owner: WP-9.

A :class:`Schedule` is a list of :class:`ReplayStep` — "hold this quote for N blocks, with these
pool biases and this signalling share" — preceded by the bootstrap every devnet chain needs
(funding blocks, then a full activation). The runner executes it on nodes; the differential suite
(:mod:`ybcal.devnet.diff`) hands the identical schedule to the simulator, so both see the same
inputs block for block.

The five PLAN §6.4 scenarios are defined here at regtest scale (``calm``, ``crash-70``,
``hashrate-drop``, ``attestor-outage-1``, ``oracle-attack-34``). They are deterministic in the seed.
When WP-2's scenario library lands, :func:`steps_from_path` turns any block-resolution
:class:`~ybcal.types.PricePath` into steps, so a WP-2 scenario can be replayed too.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.types import PricePath
from ybcal.units import MICRO_USD_PER_USD, PRICE_MAX, PRICE_MIN

OWNER_WP = "WP-9"

#: Blocks node 0 mines before anything else so its wallet has mature coinbase (devnet ``up``).
FUNDING_BLOCKS = 101
#: Default starting price ($50, the devnet launcher's default).
DEFAULT_PRICE = 50 * MICRO_USD_PER_USD


@dataclass(frozen=True)
class ReplayStep:
    """Hold one price for ``blocks`` blocks.

    ``price`` is µUSD per YEC; ``0`` clears every pool's quote (a feed outage: blocks then carry
    signal-only tags). ``pool_bias_bps`` maps a pool index to a quote bias (an oracle attacker).
    ``pool_weights`` sets each pool's share of the tagged blocks (default equal). ``signal_share_bps``
    is the share of blocks mined by the signalling pools; the rest come from the *dark* miner
    (a ``-yellowbacksignal=0`` node with no payout key) — ``None`` means 100 %.
    ``attestors_down`` lists attestor indices whose agents are stopped during the step (launcher
    mode only). ``label`` is free text for logs.
    """

    price: int
    blocks: int
    pool_bias_bps: Mapping[int, int] = field(default_factory=dict)
    pool_weights: Sequence[int] | None = None
    signal_share_bps: int | None = None
    attestors_down: tuple[int, ...] = ()
    label: str = ""
    #: wallet actions run right before the step's first block (:mod:`ybcal.devnet.actions`)
    actions: tuple[Mapping[str, Any], ...] = ()
    #: pools whose feed is stuck: they repeat their previous quote exactly (PIN-1's target)
    frozen_pools: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.blocks < 0:
            raise ValueError("blocks must be >= 0")
        if self.price and not PRICE_MIN <= self.price <= PRICE_MAX:
            raise ValueError(f"price {self.price} µUSD outside [PRICE_MIN, PRICE_MAX]")
        if self.signal_share_bps is not None and not 0 <= self.signal_share_bps <= 10_000:
            raise ValueError("signal_share_bps must be in [0, 10000]")

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        d = asdict(self)
        d["pool_bias_bps"] = {str(k): v for k, v in self.pool_bias_bps.items()}
        d["attestors_down"] = list(self.attestors_down)
        d["actions"] = [dict(a) for a in self.actions]
        d["frozen_pools"] = list(self.frozen_pools)
        return d


@dataclass(frozen=True)
class Schedule:
    """Bootstrap + scenario steps, and what the run needs."""

    name: str
    steps: tuple[ReplayStep, ...]
    needs: frozenset[str] = frozenset()  #: subset of {"dark_miner", "attestors"}
    description: str = ""
    seed: int = 0

    @property
    def total_blocks(self) -> int:
        """Blocks the schedule mines (bootstrap included)."""
        return sum(s.blocks for s in self.steps)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Schedule:
        """Inverse of :meth:`to_dict` (a kept run's ``run.json`` → the exact schedule it replayed)."""
        steps = tuple(
            ReplayStep(
                int(st["price"]),
                int(st["blocks"]),
                {int(k): int(v) for k, v in (st.get("pool_bias_bps") or {}).items()},
                tuple(st["pool_weights"]) if st.get("pool_weights") else None,
                st.get("signal_share_bps"),
                tuple(st.get("attestors_down") or ()),
                st.get("label", ""),
                tuple(dict(a) for a in st.get("actions") or ()),
                tuple(int(x) for x in st.get("frozen_pools") or ()),
            )
            for st in d["steps"]
        )
        needs = frozenset(d.get("needs", ()))
        return cls(d["name"], steps, needs, d.get("description", ""), int(d.get("seed", 0)))

    def to_dict(self) -> dict[str, Any]:
        """JSON form (written next to the run's scrape)."""
        return {
            "name": self.name,
            "description": self.description,
            "seed": self.seed,
            "needs": sorted(self.needs),
            "steps": [s.to_dict() for s in self.steps],
        }


def bootstrap_steps(params: ParamSet, price: int = DEFAULT_PRICE) -> list[ReplayStep]:
    """Funding blocks (no quote), then ``signalWindow + activationDelay + 2`` quoted, signalling
    blocks so the chain is ACTIVE (the devnet ``up`` sequence)."""
    act = int(params["signalWindow"]) + int(params["activationDelay"]) + 2
    return [ReplayStep(0, FUNDING_BLOCKS, label="funding"), ReplayStep(price, act, label="activation")]


def steps_from_prices(prices: Sequence[int], blocks_per_step: int = 1, label: str = "") -> list[ReplayStep]:
    """One step per price (µUSD), each held ``blocks_per_step`` blocks; consecutive equal prices merge."""
    out: list[ReplayStep] = []
    for p in prices:
        p = int(p)
        if out and out[-1].price == p:
            out[-1] = ReplayStep(p, out[-1].blocks + blocks_per_step, label=label)
        else:
            out.append(ReplayStep(p, blocks_per_step, label=label))
    return out


def steps_from_path(
    path: PricePath, *, index: int = 0, blocks_per_step: int | None = None
) -> list[ReplayStep]:
    """Steps from one path of a :class:`PricePath` (block resolution: 1 block per value; hour: 48
    unless ``blocks_per_step`` says otherwise — at regtest scale pass the scaled step)."""
    bps = blocks_per_step if blocks_per_step is not None else path.step_blocks
    return steps_from_prices([int(x) for x in path.prices[index]], bps, label=f"path[{index}]")


def load_price_file(file: str | Path, blocks_per_step: int = 1) -> list[ReplayStep]:
    """Steps from a JSON list of µUSD prices, or a CSV/text file with one µUSD price per line
    (a header line and a leading index column are tolerated; the last column is the price)."""
    p = Path(file)
    text = p.read_text()
    if p.suffix == ".json":
        doc = json.loads(text)
        prices = doc["prices"] if isinstance(doc, dict) else doc
        return steps_from_prices([int(x) for x in prices], blocks_per_step, label=p.stem)
    prices = []
    for line in text.splitlines():
        cell = line.strip().split(",")[-1].strip()
        if cell and cell.lstrip("-").isdigit():
            prices.append(int(cell))
    if not prices:
        raise ValueError(f"no µUSD prices in {file}")
    return steps_from_prices(prices, blocks_per_step, label=p.stem)


def _walk(rng: random.Random, start: int, n: int, vol_bps: int) -> list[int]:
    """Integer multiplicative random walk (µUSD), clamped to the valid price range."""
    out, p = [], start
    for _ in range(n):
        p = p * (10_000 + rng.randint(-vol_bps, vol_bps)) // 10_000
        p = max(PRICE_MIN, min(PRICE_MAX, p))
        out.append(p)
    return out


def _calm(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    n = 3 * int(params["pSlowWindow"])
    return steps_from_prices(_walk(rng, price, n, 50), 1, "calm")


def _crash70(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    slow, fast = int(params["pSlowWindow"]), int(params["pFastWindow"])
    pre = _walk(rng, price, slow, 50)
    last = pre[-1]
    fall = [last - (last * 7 * (i + 1)) // (10 * fast) for i in range(fast)]  # linear to 30 %
    post = _walk(rng, fall[-1], 2 * slow, 50)
    return (
        steps_from_prices(pre, 1, "pre-crash")
        + steps_from_prices(fall, 1, "crash")
        + steps_from_prices(post, 1, "post-crash")
    )


def _hashrate_drop(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    w = int(params["signalWindow"])
    prices = _walk(rng, price, 4 * w, 30)
    out = []
    for i, p in enumerate(prices):
        share = 8_000 if i < w else 4_500 if i < 3 * w else 8_000
        out.append(ReplayStep(p, 1, signal_share_bps=share, label=f"signal {share // 100}%"))
    return out


def _registrations(params: ParamSet) -> tuple[dict[str, Any], ...]:
    """``max(3, attestArmMin)`` seats, round-robin over pool nodes 0-2 (a wallet may hold several),
    each with the minimum bond and lock, so the layer arms whatever ``attestArmMin`` the set carries."""
    bond = -(-int(params["bondMin"]) // 100_000_000)  # whole YEC, rounded up
    lock = int(params["bondMinLock"])
    n = max(3, int(params["attestArmMin"]))
    return tuple({"op": "register", "node": i % 3, "bond": bond, "lock": lock} for i in range(n))


def _attestor_outage(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    """Three attestor seats (pools 0-2, emulated agents) arm the layer; node 0 mints every
    ``attestInterval + 2`` blocks so bundles are in demand; seat 0's agent is stopped for the middle
    third of two slow windows (bundle liveness, dormancy), then restarted (D-RD-DEV-4)."""
    n = 2 * int(params["pSlowWindow"])
    arm = int(params["bondMaturity"]) + int(params["attestArmDelay"]) + 4
    every = int(params["attestInterval"]) + 2
    lock = int(params["classMin[2]"])
    cents = int(params["minMint"])
    reg = _registrations(params)
    prices = _walk(rng, price, arm + n, 50)
    third = n // 3
    out = [ReplayStep(prices[0], 1, actions=reg, label="register seats")]
    out += steps_from_prices(prices[1:arm], 1, "maturity + arming")
    for i, p in enumerate(prices[arm:]):
        acts = ({"op": "mint", "node": 0, "cents": cents, "lock": lock},) if i % every == 0 else ()
        down = (0,) if third <= i < 2 * third else ()
        out.append(ReplayStep(p, 1, attestors_down=down, label="outage", actions=acts))
    return out


def _pin(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    """PIN-1 then PIN-2 on real nodes (D-RD-DEV-4). Seats arm the layer and node 0 mints every
    ``attestInterval`` blocks, so every pin window holds bundles. Phase 1: pool 1's feed freezes
    while the price climbs 15 % over one pin window (live attestors move aMint) — PIN-1 should pin
    pool 1's payout key. Phase 2: pool 1 recovers, the last seat's agent freezes and the price
    climbs again — PIN-2 should pin that seq. (A frozen seat anchors the low bundle quantile, so
    the two are exercised one after the other.)"""
    arm = int(params["bondMaturity"]) + int(params["attestArmDelay"]) + 4
    every = int(params["attestInterval"])
    pw = int(params["pinWindow"])
    mint = {"op": "mint", "node": 0, "cents": int(params["minMint"]), "lock": int(params["classMin[2]"])}
    reg = _registrations(params)

    def ramp(p0: int) -> list[int]:
        return [p0 + p0 * 15 * (i + 1) // (100 * pw) for i in range(pw)]

    calm = _walk(rng, price, arm + 2 * pw, 20)
    r1 = ramp(calm[-1])
    hold1 = _walk(rng, r1[-1], 2 * pw, 20)
    r2 = ramp(hold1[-1])
    hold2 = _walk(rng, r2[-1], 2 * pw, 20)
    out = [ReplayStep(calm[0], 1, actions=reg, label="register seats")]
    out += steps_from_prices(calm[1:arm], 1, "maturity + arming")
    tail: list[tuple[int, str, tuple[int, ...]]] = [(p, "calm", ()) for p in calm[arm:]]
    tail += [(p, "ramp 1, pool 1 frozen", (1,)) for p in r1]
    tail += [(p, "hold 1, pool 1 frozen", (1,)) for p in hold1]
    freeze_at = len(tail)
    tail += [(p, "ramp 2, last seat frozen", ()) for p in r2]
    tail += [(p, "hold 2, last seat frozen", ()) for p in hold2]
    for i, (p, label, frozen) in enumerate(tail):
        acts: tuple[dict[str, Any], ...] = (dict(mint),) if i % every == 0 else ()
        if i == freeze_at:
            acts += ({"op": "freeze", "seat": len(reg) - 1},)
        out.append(ReplayStep(p, 1, label=label, actions=acts, frozen_pools=frozen))
    return out


def _oracle_attack(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    slow, mid = int(params["pSlowWindow"]), int(params["pMidWindow"])
    pre = _walk(rng, price, slow, 50)
    attack = _walk(rng, pre[-1], 2 * mid, 50)
    post = _walk(rng, attack[-1], slow, 50)
    weights = (34, 33, 33)  # pool 0 holds 34 % of the tagged blocks and quotes 25 % high
    return (
        steps_from_prices(pre, 1, "pre-attack")
        + [ReplayStep(p, 1, pool_bias_bps={0: 2_500}, pool_weights=weights, label="attack") for p in attack]
        + steps_from_prices(post, 1, "post-attack")
    )


def _feed_outage(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    """Every pool's feed dies for 1.5 mid windows (signal-only tags), then resumes 8 % lower."""
    slow, mid = int(params["pSlowWindow"]), int(params["pMidWindow"])
    pre = _walk(rng, price, slow, 50)
    gap = (3 * mid) // 2
    post = _walk(rng, pre[-1] * 92 // 100, 2 * slow, 50)
    return [
        *steps_from_prices(pre, 1, "pre-outage"),
        ReplayStep(0, gap, label="feed outage"),
        *steps_from_prices(post, 1, "post-outage"),
    ]


def _vault_cycle(params: ParamSet, rng: random.Random, price: int) -> list[ReplayStep]:
    """The vault differential (D-RD-DEV-3): node 0 mints one vault per class, hands node 1 YED,
    redeems the class-A vault after its lock, the price falls to 20 %, a mint is attempted into the
    halt, node 1 claims whatever is claimable, the class-C vault is left claimable to the end.

    Lock lengths sit at each class's lower bound, so the claim heights follow the parameter set."""
    lo = [int(params[f"classMin[{i}]"]) for i in range(3)]
    grace, fast, mid = int(params["grace"]), int(params["pFastWindow"]), int(params["pMidWindow"])
    cents = max(int(params["minMint"]), 20_000)
    warm = _walk(rng, price, 40, 50)
    mint = {"op": "mint", "node": 0, "cents": cents}
    out = [
        *steps_from_prices(warm[:-1], 1, "warm-up"),
        ReplayStep(  # carriers at +1, the MINTs reach the mempool after it and confirm at +2
            warm[-1],
            3,
            actions=tuple({**mint, "lock": lo[i]} for i in range(3)),
            label="mint A, B, C",
        ),
        ReplayStep(
            warm[-1],
            1,
            actions=({"op": "send", "node": 0, "to": 1, "cents": cents * 3 // 2},),
            label="fund liquidator",
        ),
    ]
    # until the class-A vault's lock has passed (lockHeight = R + classMin[0]; R = mint tip - 2)
    calm = _walk(rng, warm[-1], lo[0] + 4, 30)
    out += steps_from_prices(calm, 1, "calm")
    redeem = {"op": "redeem", "node": 0, "vault": "v0"}
    out.append(ReplayStep(calm[-1], 1, actions=(redeem,), label="redeem A"))
    fall = [calm[-1] - (calm[-1] * 8 * (i + 1)) // (10 * fast) for i in range(fast)]
    out += steps_from_prices(fall, 1, "crash to 20 %")
    low = _walk(rng, fall[-1], mid, 30)
    out += steps_from_prices(low, 1, "low")
    out.append(ReplayStep(low[-1], 1, actions=({**mint, "lock": lo[0]},), label="mint into the halt"))
    # past the class-B claim height (lock B + grace from the mint), then claim, then past class C's
    b_claim = 3 + 1 + len(calm) + 1 + fast + mid + 1  # blocks already after the mint step
    wait_b = max(1, lo[1] + grace - b_claim + 4)
    low2 = _walk(rng, low[-1], wait_b, 30)
    out += steps_from_prices(low2, 1, "low, B matures")
    out.append(ReplayStep(low2[-1], 2, actions=({"op": "claim_all", "node": 1},), label="liquidator claims"))
    wait_c = max(1, lo[2] - lo[1] + 8)
    out += steps_from_prices(_walk(rng, low2[-1], wait_c, 30), 1, "low, C claimable, unclaimed")
    return out


ScenarioFn = Callable[[ParamSet, random.Random, int], list[ReplayStep]]

#: PLAN §6.4's differential suite: name → (builder, needs, description).
SCENARIOS: dict[str, tuple[ScenarioFn, frozenset[str], str]] = {
    "calm": (_calm, frozenset(), "random walk ±0.5 %/block for 3 slow windows"),
    "crash-70": (
        _crash70,
        frozenset(),
        "calm slow window, linear fall to 30 % over one fast window, 2 slow windows",
    ),
    "hashrate-drop": (
        _hashrate_drop,
        frozenset({"dark_miner"}),
        "signalling share 80 % → 45 % for two signal windows → 80 %",
    ),
    "attestor-outage-1": (
        _attestor_outage,
        frozenset({"attestors"}),
        "three seats arm the layer, mints draw bundles, seat 0's agent stopped for the middle third",
    ),
    "oracle-attack-34": (
        _oracle_attack,
        frozenset(),
        "a pool with 34 % of tagged blocks quotes +25 % for two mid windows",
    ),
    "vault-cycle": (
        _vault_cycle,
        frozenset({"wallet"}),
        "mint A/B/C, redeem A, crash to 20 %, mint into the halt, claim B, C left claimable",
    ),
    "pin": (
        _pin,
        frozenset({"attestors", "wallet"}),
        "seats arm, mints every attestInterval; pool 1 frozen through a 15 % climb, then a seat frozen",
    ),
    "feed-outage": (
        _feed_outage,
        frozenset(),
        "every feed dark for 1.5 mid windows (signal-only tags), then resumes 8 % lower",
    ),
}

#: The PLAN §6.4 suite, in order.
SUITE: tuple[str, ...] = (
    "calm",
    "crash-70",
    "hashrate-drop",
    "attestor-outage-1",
    "oracle-attack-34",
    "feed-outage",
    "vault-cycle",
    "pin",
)


def make_schedule(
    name: str,
    params: ParamSet,
    *,
    seed: int = 0,
    price: int = DEFAULT_PRICE,
    bootstrap: bool = True,
    file_blocks_per_step: int = 1,
) -> Schedule:
    """The schedule for a built-in scenario name, or for a price file (``.json`` / ``.csv``)."""
    rng = random.Random(seed)
    if name in SCENARIOS:
        fn, needs, desc = SCENARIOS[name]
        steps = fn(params, rng, price)
    elif Path(name).is_file():
        steps, needs, desc = load_price_file(name, file_blocks_per_step), frozenset(), f"price file {name}"
    else:
        raise KeyError(
            f"unknown devnet scenario {name!r} (built-in: {', '.join(SCENARIOS)}; or a price file)"
        )
    first = next((s.price for s in steps if s.price), price)
    pre = bootstrap_steps(params, first) if bootstrap else []
    return Schedule(name, tuple(pre + steps), needs, desc, seed)


def schedule_prices(schedule: Schedule) -> PricePath:
    """The schedule's reference price per block as a block-resolution :class:`PricePath`
    (``0`` where no quote is held) — the simulator's input path."""
    from datetime import datetime

    per_block = (
        np.concatenate([np.full(s.blocks, s.price, dtype=np.int64) for s in schedule.steps])
        if schedule.steps
        else np.zeros(0, dtype=np.int64)
    )
    return PricePath(
        datetime(2026, 1, 1, tzinfo=UTC),
        "block",
        per_block,
        "scenario",
        {"scenario": schedule.name, "seed": schedule.seed},
    )
