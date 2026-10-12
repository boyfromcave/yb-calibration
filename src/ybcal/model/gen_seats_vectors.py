"""Regenerate ``seats_vectors.json`` from the node's own Python model (ycash-dd ``q/attest``).

Usage: ``python -m ybcal.model.gen_seats_vectors <ycash-dd tree> src/ybcal/model/seats_vectors.json``.
It imports ``qa/rpc-tests/test_framework/yellowback_model.py`` from the tree given and writes
the inputs and the model's outputs; ``tests/model/test_seats.py`` and ``ybcal verify`` hold
:mod:`ybcal.model.seats` to them bit for bit. Run by hand only (never imported by ybcal).
"""

import json
import random
import subprocess
import sys

NODE = sys.argv[1]
OUT = sys.argv[2]
sys.path.insert(0, NODE + "/qa/rpc-tests")
from test_framework import yellowback_model as ym  # noqa: E402

commit = subprocess.check_output(["git", "-C", NODE, "rev-parse", "HEAD"], text=True).strip()
rng = random.Random(20261011)
COIN = ym.COIN
xs = [
    0,
    1,
    2,
    3,
    4,
    5,
    7,
    1023,
    1024,
    1025,
    1152,
    2304,
    9216,
    421632,
    (1 << 31) - 1,
    1 << 31,
    (1 << 32) - 1,
    1 << 32,
    (1 << 32) + 1,
    (1 << 40),
    (1 << 63) - 1,
    (1 << 64) - 1,
]
xs += [rng.randrange(1, 1 << 20) for _ in range(60)]
xs += [rng.randrange(1, 1 << 64) for _ in range(60)]
xs += list(range(1, 513))
log2 = [[x, ym.log2_q16(x)] for x in xs]
ucaps = [
    (1152, 420480),
    (1, 1),
    (1, 420480),
    (1152, 1),
    (8, 64),
    (100, 50_000),
    (0, 420480),
    (1152, 0),
    (-5, 10),
    (7, 1 << 40),
]
factor = []
for u, cap in ucaps:
    ages = [-10, 0, 1, 2, 8, 16128, 1152, 8064, 210240, 420479, 420480, 420481, 3 * 420480]
    ages += [rng.randrange(0, 2 * max(1, cap)) for _ in range(60)]
    factor += [[a, u, cap, ym.seat_age_factor_q16(a, u, cap)] for a in ages]
weight = []
for _ in range(300):
    b = rng.choice([0, -1, 1, 5000 * COIN, 20000 * COIN, ym.MAX_MONEY, rng.randrange(1, 30000 * COIN)])
    a = rng.randrange(-100, 900000)
    weight.append([b, a, 1152, 420480, ym.seat_weight(b, a, 1152, 420480)])
p = ym.Params.mainnet(1, attestor_set="5e" * 32)
pr = ym.Params.regtest(1, 0, 0, "5e" * 32)
evict = []
for net, pp in (("main", p), ("regtest", pr)):
    for _ in range(150):
        H = rng.randrange(500_000, 3_000_000)
        lw = rng.choice([1152, 2304, 16])
        n = rng.randrange(0, 16)
        keys = rng.sample(range(256), n)
        members = []
        for k in keys:
            bond = rng.choice(
                [
                    pp.bond_min_floor_zat,
                    pp.bond_min_cap_zat,
                    rng.randrange(pp.bond_min_floor_zat, 2 * pp.bond_min_cap_zat),
                ]
            )
            join = H - rng.choice([0, 1, 16128, 420480, 900000, rng.randrange(0, 500000)])
            last = H - rng.choice([0, lw - 1, lw, lw + 1, rng.randrange(0, 3 * lw)])
            members.append([bytes([2, k]) + bytes(31), bond, join, last])
        nb = rng.choice(
            [
                pp.bond_min_floor_zat - 1,
                pp.bond_min_floor_zat,
                pp.bond_min_cap_zat,
                rng.randrange(0, 3 * pp.bond_min_cap_zat),
            ]
        )
        v = ym.eviction_victim([tuple(m) for m in members], H, nb, lw, pp)
        evict.append(
            {
                "net": net,
                "height": H,
                "liveness_window": lw,
                "new_bond_zat": nb,
                "members": [[m[0].hex(), m[1], m[2], m[3]] for m in members],
                "victim": None if v is None else v.hex(),
            }
        )
bondmin = []
for net, pp in (("main", p), ("regtest", pr)):
    for c in [
        0,
        -5,
        1,
        900_000 * COIN - 1,
        900_000 * COIN,
        900_000 * COIN + 1,
        3_600_000 * COIN,
        3_600_001 * COIN,
        ym.MAX_MONEY,
    ]:
        bondmin.append([net, c, ym.bond_min_from_collateral_zat(c, pp)])
    for _ in range(200):
        c = rng.randrange(0, 10_000_000 * COIN)
        bondmin.append([net, c, ym.bond_min_from_collateral_zat(c, pp)])
params = {}
for net, pp in (("main", p), ("regtest", pr)):
    params[net] = {
        k: getattr(pp, k)
        for k in (
            "n_slots",
            "bond_min_floor_zat",
            "bond_min_cap_zat",
            "bond_share_bps",
            "bond_min_lock",
            "bond_maturity",
            "age_cap",
            "seat_age_unit",
            "seat_age_cap",
            "seat_margin_bps",
            "evict_margin_bps",
            "attestor_seats",
            "attestor_liveness_min",
        )
    }
doc = {
    "source": "ycash-dd q/attest qa/rpc-tests/test_framework/yellowback_model.py",
    "commit": commit,
    "params": params,
    "log2_q16": log2,
    "seat_age_factor_q16": factor,
    "seat_weight": weight,
    "eviction_victim": evict,
    "bond_min_from_collateral_zat": bondmin,
}
with open(OUT, "w") as fh:
    json.dump(doc, fh, separators=(",", ":"))
print("ok", commit, len(log2), len(factor), len(weight), len(evict), len(bondmin))
