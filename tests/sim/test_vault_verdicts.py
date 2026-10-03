"""MINT / RED / NOT-1 verdicts of ``ybcal.sim.vaults`` == the vendored reference model on crafted states.

Each case builds the same transaction twice — as the book's abstraction (``MintTx`` / ``SpendTx`` +
``Snap``) and as a real reference transaction (vault script, payload, fee outputs) against a
``YellowbackModel`` whose snapshot / totals / tags are set to the crafted state — and asserts the
verdict strings are identical. Bundles (ARMED) are injected into the reference through its
``_bundle`` / ``_attest_fee_ok`` seams, so the ARMED rule *order* (MINT-9 → AFEE-1 → MINT-5 at
min(xMint, aMint) → MINT-10; RED-1 bundle → RED-4(b) notice window) is compared, not the signatures.
"""

from __future__ import annotations

import pytest

from ybcal.model import kernels as K
from ybcal.model import reference as R
from ybcal.params.paramset import regtest
from ybcal.sim import vaults as VB

from .refbuild import PAYEE, mint_tx, owner_pubkey, patch_bundle, ref_snapshot, ref_vault, spend_tx, txid

START = 10
PS = regtest().replace(startHeight=START, supplyCapBps=1500, sigmaRefBps=10_000)
RP = VB.RuleParams.of(PS)
H = 200
REFH = 190
PRICE = 2_000_000  # $2


def model_for(snap: VB.Snap, supply: int = 0, eligible: bool = True, extra_snaps=()) -> R.YellowbackModel:
    p = R.Params.regtest(start_height=START, sigma_ref_bps=10_000, supply_cap_bps=1500)
    m = R.YellowbackModel(p)
    m.snapshots[snap.height] = ref_snapshot(snap)
    for s in extra_snaps:
        m.snapshots[s.height] = ref_snapshot(s)
    m.totals.supply_cents = supply
    if eligible:
        m.tags[snap.height] = R.TagRecord(PAYEE, PRICE, True, 0)
    return m


def snap(**kw) -> VB.Snap:
    base = dict(
        height=REFH,
        active=True,
        halt_mask=0,
        x_mint=PRICE,
        x_claim=PRICE,
        p_fast=PRICE,
        sigma_mult_bps=10_000,
        issued_zat=10**15,
        armed=False,
        eligible=True,
    )
    base.update(kw)
    return VB.Snap(**base)


def required(cents, cls=0, sigma=10_000, price=PRICE) -> int:
    return K.required_zat(cents, K.min_ratio_bps(RP.base_ratio[cls], sigma), price)


def good_mint(cls=0, cents=50_000, **kw) -> VB.MintTx:
    lock = REFH + RP.class_min[cls] + 1
    coll = max(required(cents, cls), 4 * RP.fee_min)
    base = dict(
        term_class=cls,
        cents=cents,
        lock_height=lock,
        ref_height=REFH,
        collateral_zat=coll,
        fee_zat=RP.fee(coll),
    )
    base.update(kw)
    return VB.MintTx(**base)


def ref_mint_verdict(tx: VB.MintTx, s: VB.Snap, supply=0, height=H, bundle=None, extra_snaps=()):
    m = model_for(s, supply, s.eligible, extra_snaps)
    patch_bundle(m, bundle)
    t, pl, idx = mint_tx(tx, RP.grace, owner_pubkey(1), txid("mint", 1))
    assert pl is not None
    return m._mint_verdict(t, height, pl, idx, {})


def check_mint(tx, s, supply=0, height=H, bundle=None, expect=None):
    mine = VB.mint_verdict(RP, height, VB.MintTx(**{**tx.__dict__, "bundle": bundle}), s, supply)
    ref = ref_mint_verdict(tx, s, supply, height, bundle)
    assert mine == ref, (mine, ref)
    if expect is not None:
        assert mine == expect
    return mine


# ---------------------------------------------------------------------------------------------------
# MINT


def test_mint_ok_each_class():
    for c in range(3):
        check_mint(good_mint(c), snap(), expect=VB.OK)


@pytest.mark.parametrize(
    "cents,expect",
    [
        (RP.min_mint - 1, VB.BAD_MINT_AMOUNT),
        (RP.max_mint + 1, VB.BAD_MINT_AMOUNT),
        (RP.min_mint, VB.OK),
        (RP.max_mint, VB.OK),
    ],
)
def test_mint2_amount(cents, expect):
    tx = good_mint(0, cents)
    check_mint(tx, snap(), expect=expect)


def test_mint2_class_ref_lock():
    s = snap()
    # bad class: the reference encodes the byte; class 3 is outside classRange
    tx = VB.MintTx(**{**good_mint(0).__dict__, "term_class": 3})
    assert VB.mint_verdict(RP, H, tx, s, 0) == VB.BAD_MINT_CLASS
    assert ref_mint_verdict(tx, s) == "bad-mint-class"
    # ref window: R = H is too new, R < H − refWindow too old, R < startHeight
    for ref, h in ((H, H), (H - RP.ref_window - 1, H), (START - 1, START + 5)):
        s2 = snap(height=max(ref, 0))
        tx2 = VB.MintTx(**{**good_mint(0).__dict__, "ref_height": ref, "lock_height": ref + RP.class_min[0]})
        check_mint(tx2, s2, height=h, expect=VB.BAD_MINT_REF_HEIGHT)
    # lock outside the class range / not after R
    for lock in (REFH + RP.class_min[0] - 1, REFH + RP.class_max[0] + 1, REFH):
        check_mint(
            VB.MintTx(**{**good_mint(0).__dict__, "lock_height": lock}), s, expect=VB.BAD_MINT_LOCK_HEIGHT
        )
    # boundaries inside the range are fine
    for lock in (REFH + RP.class_min[0], REFH + RP.class_max[0]):
        check_mint(VB.MintTx(**{**good_mint(0).__dict__, "lock_height": lock}), s, expect=VB.OK)


@pytest.mark.parametrize(
    "mask,cls,sigma,expect",
    [
        (VB.HALT_NOT_ACTIVE, 0, 10_000, VB.MINT_NOT_ACTIVE),
        (VB.HALT_NO_PRICE, 0, 10_000, VB.MINT_HALTED_NO_PRICE),
        (VB.HALT_PARTICIPATION, 0, 10_000, VB.MINT_HALTED_PARTICIPATION),
        (VB.HALT_ENFORCEMENT, 0, 10_000, VB.MINT_HALTED_PARTICIPATION),
        (VB.HALT_GLOBAL_RATIO, 1, 10_000, VB.MINT_HALTED_GLOBAL_RATIO),  # class B: 40,000 < recap 50,000
        (VB.HALT_GLOBAL_RATIO, 0, 10_000, VB.OK),  # W16: class A at σ=1 is exactly recap
        (VB.HALT_GLOBAL_RATIO, 2, 16_667, VB.OK),  # class C at σ 1.6667: 50,001 ≥ recap
        (VB.HALT_GLOBAL_RATIO, 2, 16_666, VB.MINT_HALTED_GLOBAL_RATIO),  # 49,998 < recap
        (VB.HALT_DIVERGENCE, 0, 10_000, VB.MINT_HALTED_DIVERGENCE),
        (VB.HALT_GLOBAL_RATIO | VB.HALT_DIVERGENCE, 0, 10_000, VB.MINT_HALTED_DIVERGENCE),
        (1 << 7, 0, 10_000, VB.MINT_NOT_ACTIVE),  # an unknown bit
    ],
)
def test_mint4_halts_and_recap_exemption(mask, cls, sigma, expect):
    s = snap(halt_mask=mask, sigma_mult_bps=sigma)
    tx = good_mint(cls)
    coll = max(required(tx.cents, cls, sigma), 4 * RP.fee_min)
    tx = VB.MintTx(**{**tx.__dict__, "collateral_zat": coll, "fee_zat": RP.fee(coll)})
    check_mint(tx, s, expect=expect)


def test_mint4_not_active_snapshot():
    check_mint(good_mint(0), snap(active=False), expect=VB.MINT_NOT_ACTIVE)


def test_mint5_collateral_and_floor_and_unsatisfiable():
    s = snap()
    tx = good_mint(0, 50_000)
    req = required(50_000)
    check_mint(VB.MintTx(**{**tx.__dict__, "collateral_zat": req, "fee_zat": RP.fee(req)}), s, expect=VB.OK)
    check_mint(
        VB.MintTx(**{**tx.__dict__, "collateral_zat": req - 1, "fee_zat": RP.fee(req)}),
        s,
        expect=VB.BAD_MINT_COLLATERAL,
    )
    # the 4·feeMin floor binds (feeMin raised to 10 YEC on both sides so a minMint vault hits it)
    small = good_mint(0, RP.min_mint)
    rp_f = VB.RuleParams(**{**RP.__dict__, "fee_min": 10 * 10**8})
    s_hi = snap(x_mint=90_000_000, x_claim=90_000_000, p_fast=90_000_000)
    r = required(RP.min_mint, 0, 10_000, 90_000_000)
    assert r < 4 * rp_f.fee_min
    for coll, want in ((r, VB.BAD_MINT_COLLATERAL), (4 * rp_f.fee_min, VB.OK)):
        tx_f = VB.MintTx(**{**small.__dict__, "collateral_zat": coll, "fee_zat": rp_f.fee(coll)})
        m = model_for(s_hi)
        m.params.fee_min = rp_f.fee_min
        patch_bundle(m, None)
        t, pl, idx = mint_tx(tx_f, RP.grace, owner_pubkey(1), txid("mint", 9))
        assert VB.mint_verdict(rp_f, H, tx_f, s_hi, 0) == m._mint_verdict(t, H, pl, idx, {}) == want
    # K14: the requirement exceeds MAX_MONEY at a dust price with the σ cap
    s_lo = snap(x_mint=100, x_claim=100, p_fast=100, sigma_mult_bps=30_000)
    check_mint(
        VB.MintTx(**{**good_mint(0, RP.max_mint).__dict__, "collateral_zat": 10**15}),
        s_lo,
        expect=VB.MINT_UNSATISFIABLE,
    )


def test_mint6_soft_cap_live_supply_and_exemption():
    issued = 10**13  # 100,000 YEC at $2 → capCents 2e7, × 15 % → 3,000,000 cents
    s = snap(issued_zat=issued)
    cap = K.supply_cap_cents(issued, PRICE, RP.supply_cap_bps)
    assert cap == 3_000_000
    txb = good_mint(1, 50_000)
    check_mint(txb, s, supply=cap - 50_000, expect=VB.OK)  # exactly at the cap: admitted
    check_mint(txb, s, supply=cap - 49_999, expect=VB.MINT_SUPPLY_CAP)  # one cent over: refused
    check_mint(good_mint(0, 50_000), s, supply=cap, expect=VB.OK)  # class A ≥ recap: W20 exemption
    # σ multiplier lifts class C over the recap floor: exempt too
    s3 = snap(issued_zat=issued, sigma_mult_bps=17_000)
    tx3 = good_mint(2, 50_000)
    coll = required(50_000, 2, 17_000)
    check_mint(
        VB.MintTx(**{**tx3.__dict__, "collateral_zat": coll, "fee_zat": RP.fee(coll)}),
        s3,
        supply=cap,
        expect=VB.OK,
    )
    # no cap at all (capBps 0 on the rule params) admits anything
    rp0 = VB.RuleParams(**{**RP.__dict__, "supply_cap_bps": 0})
    assert VB.mint_verdict(rp0, H, txb, s, 10**12) == VB.OK


def test_mint8_fee_and_fee0():
    s = snap()
    tx = good_mint(0)
    check_mint(VB.MintTx(**{**tx.__dict__, "fee_zat": None}), s, expect=VB.BAD_MINT_FEE)
    check_mint(
        VB.MintTx(**{**tx.__dict__, "fee_zat": RP.fee(tx.collateral_zat) - 1}), s, expect=VB.BAD_MINT_FEE
    )
    # FEE-0: E(R) empty → no fee output needed
    s0 = snap(eligible=False)
    check_mint(VB.MintTx(**{**tx.__dict__, "fee_zat": None}), s0, expect=VB.OK)


ARMED_CASES = [
    ("no-bundle", None, VB.MINT9_NO_BUNDLE),
    ("bad-sig", VB.Bundle(None, None, ok=False, reason="sig"), "mint9-bundle-sig"),
    ("stat", VB.Bundle(None, None), "mint9-bundle-stat"),
    ("ok", VB.Bundle(PRICE, PRICE), VB.OK),
    ("a-lower-collateral-short", VB.Bundle(PRICE // 2, PRICE // 2), VB.BAD_MINT_COLLATERAL),
    ("diverged", VB.Bundle(PRICE * 2, PRICE * 2), VB.MINT10_DIVERGED),
    ("within-band", VB.Bundle(PRICE * 115 // 100, PRICE), VB.OK),
    ("just-out", VB.Bundle(PRICE * 115 // 100 + 1, PRICE), VB.MINT10_DIVERGED),
]


@pytest.mark.parametrize("name,bundle,expect", ARMED_CASES, ids=[c[0] for c in ARMED_CASES])
def test_mint_armed_order(name, bundle, expect):
    s = snap(armed=True)
    tx = good_mint(0)
    tx = VB.MintTx(**{**tx.__dict__, "attest_fee_zat": RP.attest_fee(tx.collateral_zat)})
    check_mint(tx, s, bundle=bundle, expect=expect)


def test_mint_armed_afee():
    s = snap(armed=True)
    tx = good_mint(0)
    b = VB.Bundle(PRICE, PRICE)
    check_mint(VB.MintTx(**{**tx.__dict__, "attest_fee_zat": None}), s, bundle=b, expect=VB.AFEE1_FEE)
    low = RP.attest_fee(tx.collateral_zat) - 1
    check_mint(VB.MintTx(**{**tx.__dict__, "attest_fee_zat": low}), s, bundle=b, expect=VB.AFEE1_FEE)
    # ARMED: MINT-6 still precedes MINT-9 (the cap reads xMint, R15)
    s2 = snap(armed=True, issued_zat=10**13)
    txb = VB.MintTx(**{**good_mint(1, 50_000).__dict__, "attest_fee_zat": None})
    check_mint(txb, s2, supply=3_000_000, bundle=None, expect=VB.MINT_SUPPLY_CAP)


# ---------------------------------------------------------------------------------------------------
# RED


def mk_vault(cents=50_000, cls=0, coll=None, mint_h=100, lock=None) -> VB.VaultRecord:
    lock = lock if lock is not None else mint_h + RP.class_min[cls]
    coll = coll if coll is not None else max(required(cents, cls), 4 * RP.fee_min)
    return VB.VaultRecord(cls, lock, lock + RP.grace, coll, cents, mint_h, mint_h - 1)


def ref_red(
    vault: VB.VaultRecord,
    tx: VB.SpendTx,
    s: VB.Snap,
    height: int,
    bundle=None,
    notice_ref=None,
    yed_token_cents=None,
):
    m = model_for(s, 0, s.eligible)
    patch_bundle(m, bundle)
    owner = owner_pubkey(2)
    vop = (txid("mint", 2), 0)
    top = (txid("mint", 2), 1)
    m.vaults[vop] = ref_vault(vault, owner)
    m.tokens[top] = R.Token(tx.yed_in if yed_token_cents is None else yed_token_cents, 10_000, b"", 1)
    if notice_ref is not None:
        m.notices[vop] = R.NoticeRecord(notice_ref + 1, notice_ref, 1)
    t, pl, idx = spend_tx(tx, vop, top, vault, owner, RP.grace, txid("spend", 2))
    path = R.spend_path(t.vin[0].script_sig)
    outpoints = [(i.prev_txid, i.prev_n) for i in t.vin]
    return m._red_verdict(t, height, pl, idx, [vop], outpoints, path, tx.yed_in, {})


def check_red(vault, tx, s, height, bundle=None, notice_ref=None, expect=None):
    tx = VB.SpendTx(**{**tx.__dict__, "bundle": bundle})
    mine = VB.red_verdict(RP, height, vault, tx, s, notice_ref)
    ref = ref_red(vault, tx, s, height, bundle, notice_ref)
    assert mine.verdict == ref, (mine, ref)
    if expect is not None:
        assert mine.verdict == expect
    return mine


def owner_spend(v, ref=REFH, **kw):
    base = dict(path="owner", ref_height=ref, yed_in=v.minted_cents, fee_zat=RP.fee(v.collateral_zat))
    base.update(kw)
    return VB.SpendTx(**base)


def test_red_owner_ok_and_burns():
    v = mk_vault()
    s = snap()
    check_red(v, owner_spend(v), s, H, expect=VB.OK)
    check_red(v, owner_spend(v, yed_in=v.minted_cents - 1), s, H, expect=VB.VAULT_SPEND_SHORT_BURN)
    check_red(v, owner_spend(v, yed_in=0), s, H, expect=VB.VAULT_SPEND_MISSING_BURN)
    # assigning change back reduces the burn: RED-2 counts yedIn − assigned
    check_red(v, owner_spend(v, yed_in=v.minted_cents + 100, assigned=(100,)), s, H, expect=VB.OK)
    check_red(
        v,
        owner_spend(v, yed_in=v.minted_cents + 100, assigned=(101,)),
        s,
        H,
        expect=VB.VAULT_SPEND_SHORT_BURN,
    )


def test_red1_malformed():
    v = mk_vault()
    s = snap()
    check_red(v, owner_spend(v, redeem_payload=False, yed_in=0), s, H, expect=VB.VAULT_SPEND_MALFORMED)
    check_red(v, owner_spend(v, path=None), s, H, expect=VB.VAULT_SPEND_MALFORMED)
    check_red(v, owner_spend(v, ref=H), s, H, expect=VB.VAULT_SPEND_MALFORMED)
    check_red(
        v,
        owner_spend(v, ref=H - RP.ref_window - 1),
        snap(height=H - RP.ref_window - 1),
        H,
        expect=VB.VAULT_SPEND_MALFORMED,
    )
    check_red(
        v, owner_spend(v, yed_in=v.minted_cents + 1, assigned=(1,)), s, H, expect=VB.VAULT_SPEND_MALFORMED
    )


def test_red3_fees():
    v = mk_vault()
    s = snap()
    check_red(v, owner_spend(v, fee_zat=None), s, H, expect=VB.VAULT_SPEND_BAD_FEE)
    check_red(v, owner_spend(v, fee_zat=RP.fee(v.collateral_zat) - 1), s, H, expect=VB.VAULT_SPEND_BAD_FEE)
    check_red(v, owner_spend(v, fee_payee_ok=False), s, H, expect=VB.VAULT_SPEND_BAD_PAYEE)
    check_red(v, owner_spend(v, fee_zat=None), snap(eligible=False), H, expect=VB.OK)  # FEE-0


def claim_spend(v, **kw):
    base = dict(path="claim", ref_height=REFH, yed_in=v.minted_cents, fee_zat=RP.fee(v.collateral_zat))
    base.update(kw)
    return VB.SpendTx(**base)


def test_red4a_threshold_boundary_and_red5():
    v = mk_vault(cents=10_000, coll=6_000 * 10**8)  # the worked example: 6,000 YEC backing $100
    u = K.underwater_price(v.collateral_zat, v.minted_cents, RP.claim_threshold_bps)
    assert u == 18_333
    # at the boundary: underwater, so path (a); RED-5 residual is far below residualMin? compute
    s_u = snap(x_claim=u, x_mint=u, p_fast=u)
    out = VB.red_verdict(RP, H, v, claim_spend(v, owner_paid_zat=0), s_u)
    ref = ref_red(v, claim_spend(v, owner_paid_zat=0), s_u, H)
    assert out.verdict == ref
    resid = K.residual_zat(v.collateral_zat, K.claimant_max_zat(v.minted_cents, RP.claim_threshold_bps, u))
    assert out.residual_zat == resid
    if resid >= RP.residual_min_zat:
        assert out.verdict == VB.RED5_RESIDUAL
        check_red(v, claim_spend(v, owner_paid_zat=resid), s_u, H, expect=VB.OK)
        check_red(v, claim_spend(v, owner_paid_zat=resid - 1), s_u, H, expect=VB.RED5_RESIDUAL)
    else:
        assert out.verdict == VB.OK
    # one µUSD above: not underwater
    s_n = snap(x_claim=u + 1, x_mint=u + 1, p_fast=u + 1)
    check_red(
        v, claim_spend(v, owner_paid_zat=v.collateral_zat), s_n, H, expect=VB.VAULT_CLAIM_NOT_UNDERWATER
    )
    # undefined pClaim is never underwater (M1)
    check_red(v, claim_spend(v), snap(x_claim=None), H, expect=VB.VAULT_CLAIM_NOT_UNDERWATER)


def test_red5_residual_is_zero_under_a():
    """Under RED-4(a) the claimant's cap uses the same threshold that made the vault underwater, so
    ``claimantMax ≥ collateral`` and the residual is 0 at every underwater pClaim: RED-5 only bites
    on RED-4(b) (margin 10^4)."""
    v = mk_vault(cents=10_000, coll=6_000 * 10**8)
    for p in (1_000, 10_000, 18_000, 18_333):
        out = check_red(v, claim_spend(v), snap(x_claim=p, x_mint=p, p_fast=p), H, expect=VB.OK)
        assert out.claim_path == "a" and out.residual_zat == 0


def test_red_claim_armed_bundle_and_emergency_window():
    v = mk_vault(cents=10_000, coll=6_000 * 10**8)
    u_e = K.underwater_price(v.collateral_zat, v.minted_cents, RP.emergency_ratio_bps)
    xc = 30_000  # pools still high: not underwater under (a) at 110 %
    s = snap(armed=True, x_claim=xc, x_mint=xc, p_fast=xc)
    afee = RP.attest_fee(v.collateral_zat)
    tx = claim_spend(v, attest_fee_zat=afee, owner_paid_zat=v.collateral_zat)
    check_red(v, tx, s, H, bundle=None, expect="red1-bundle-shape")
    check_red(v, tx, s, H, bundle=VB.Bundle(1, None), expect="red1-bundle-stat")
    low = VB.Bundle(u_e, u_e)  # attestors see the crash: pEmerg = min(x, a) underwater at 105 %
    check_red(
        v, VB.SpendTx(**{**tx.__dict__, "attest_fee_zat": afee - 1}), s, H, bundle=low, expect=VB.AFEE1_FEE
    )
    # (a) uses max(x, a) = 30,000: not underwater; (b) needs a persisted notice
    check_red(v, tx, s, H, bundle=low, notice_ref=None, expect=VB.VAULT_CLAIM_NOT_UNDERWATER)
    for d, ok in (
        (RP.emergency_persist - 1, False),
        (RP.emergency_persist, True),
        (RP.emergency_notice_ttl, True),
        (RP.emergency_notice_ttl + 1, False),
    ):
        out = check_red(v, tx, s, H, bundle=low, notice_ref=REFH - d)
        assert (out.verdict == VB.OK) == ok, (d, out)
        if ok:
            assert out.claim_path == "b"
    # pEmerg one above the emergency boundary: (b) fails
    hi = VB.Bundle(u_e + 1, u_e + 1)
    check_red(
        v, tx, s, H, bundle=hi, notice_ref=REFH - RP.emergency_persist, expect=VB.VAULT_CLAIM_NOT_UNDERWATER
    )
    # unarmed: a notice never helps ((b) is false before arming)
    s0 = snap(armed=False, x_claim=xc, x_mint=xc, p_fast=xc)
    check_red(
        v,
        claim_spend(v, owner_paid_zat=v.collateral_zat),
        s0,
        H,
        notice_ref=REFH - RP.emergency_persist,
        expect=VB.VAULT_CLAIM_NOT_UNDERWATER,
    )


def test_red5_under_b_uses_margin_one():
    v = mk_vault(cents=10_000, coll=6_000 * 10**8)
    u_e = K.underwater_price(v.collateral_zat, v.minted_cents, RP.emergency_ratio_bps)
    s = snap(armed=True, x_claim=30_000, x_mint=30_000, p_fast=30_000)
    b = VB.Bundle(u_e, u_e)
    tx = claim_spend(v, attest_fee_zat=RP.attest_fee(v.collateral_zat), owner_paid_zat=0)
    out = check_red(v, tx, s, H, bundle=b, notice_ref=REFH - RP.emergency_persist)
    want = K.residual_zat(v.collateral_zat, K.claimant_max_zat(v.minted_cents, 10_000, 30_000))
    assert out.residual_zat == want and out.verdict == VB.RED5_RESIDUAL


# ---------------------------------------------------------------------------------------------------
# NOT-1


def test_notice_verdict_matches_reference():
    v = mk_vault(cents=10_000, coll=6_000 * 10**8)
    u_e = K.underwater_price(v.collateral_zat, v.minted_cents, RP.emergency_ratio_bps)
    for armed, a, standing, want in (
        (True, u_e, None, True),
        (True, u_e + 1, None, False),
        (False, u_e, None, False),
        (True, u_e, H - RP.emergency_notice_ttl, False),
        (True, u_e, H - RP.emergency_notice_ttl - 1, True),
    ):
        s = snap(armed=armed, x_claim=30_000)
        b = VB.Bundle(a, a)
        mine = VB.notice_verdict(RP, H, v, REFH, s, b, standing)
        m = model_for(s)
        patch_bundle(m, b)
        op = (txid("mint", 3), 0)
        m.vaults[op] = ref_vault(v, owner_pubkey(3))
        if standing is not None:
            m.notices[op] = R.NoticeRecord(standing, standing - 1, 1)
        pl = R.Payload(R.PAYLOAD_CLAIM_NOTICE)
        pl.vault_txid, pl.vault_vout, pl.ref_height = op[0], 0, REFH
        rec = R.TxLogRecord(H)
        ref = m._apply_notice(R.Tx("n", [], []), H, rec, pl)
        assert mine == ref == want, (armed, a, standing, mine, ref)


def test_path_open_cltv():
    v = mk_vault()
    assert not VB.path_open(v, v.lock_height, "owner")
    assert VB.path_open(v, v.lock_height + 1, "owner")
    assert not VB.path_open(v, v.claim_height, "claim")
    assert VB.path_open(v, v.claim_height + 1, "claim")


def test_wallet_collateral_scalar_vector_and_txbuilder_agree():
    """txbuilder.cpp:1095-1103: max(RequiredCollateralRounded, 4·feeMin) up to 1,000 zat (+ buffer)."""
    import numpy as np

    rng = np.random.default_rng(9)
    m = 400
    cents = rng.integers(RP.min_mint, RP.max_mint + 1, m)
    cls = rng.integers(0, 3, m)
    sig = rng.integers(10_000, 30_001, m)
    pm = np.exp(rng.uniform(np.log(100), np.log(10**8), m)).astype(np.int64)
    buf = rng.integers(0, 3_000, m) * (rng.random(m) < 0.5)
    vec = VB.wallet_collateral(RP, cents, cls, sig, pm, buf)
    for i in range(m):
        s = VB.wallet_collateral_int(RP, int(cents[i]), int(cls[i]), int(sig[i]), int(pm[i]), int(buf[i]))
        assert s == int(vec[i]) or (s == -1 and vec[i] == VB.UNDEF)
        mr = K.min_ratio_bps(RP.base_ratio[int(cls[i])], int(sig[i]))
        r = K.required_zat_rounded(int(cents[i]), mr, int(pm[i]))
        if r is None:
            assert s == -1
            continue
        want = max(r, 4 * RP.fee_min)
        want += -want % 1000
        if buf[i] == 0:
            assert s == want
        else:
            assert s >= want and s % 1000 == 0
        if s > 0:  # the wallet's collateral always passes MINT-5 at its own snapshot
            assert s >= K.required_zat(int(cents[i]), mr, int(pm[i])) and s >= 4 * RP.fee_min
