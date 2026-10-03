"""Parser unit tests on small synthetic sources, plus drift detection."""

from __future__ import annotations

import dataclasses

import pytest

from ybcal.params.extract import (
    ExtractError,
    check_drift,
    eval_expr,
    extract_sources,
    parse_constants,
    parse_struct_fields,
    strip_comments,
)
from ybcal.params.registry import REGISTRY

HEADER = """
namespace yellowback {
static const int NUM_CLASSES = 3;   // classes
static const CAmount TOKEN_VALUE = 10000;
static const unsigned char TAG_MAGIC[4] = { 0x59, 0x45, 0x44, 0x21 };
static const int REF_WINDOW = 40;
enum class BundleCarrier : uint8_t { SCRIPTSIG = 0, OP_RETURN = 1, EITHER = 2, };
struct Params
{
    std::string network;   //!< name
    int startHeight;       /* first; height */
    std::vector<unsigned char> addressVersion;
    int a, b;
    int arr[NUM_CLASSES];
    CAmount bond;
    CAmount token;
    int ref;
    bool req;
    BundleCarrier carrier;
    Params();
    bool IsConfigured() const { return startHeight > 0; }
    int MinFill(int w) const
    {
        if (w == a) return b;
        return a;
    }
};
Params RegtestParams(int startHeight, int sigmaRefBps, int supplyCapBps, int enforceUntil,
                     int attestArmMin = 3, BundleCarrier bundleCarrier = BundleCarrier::SCRIPTSIG);
}
"""

CPP = """
namespace {
void SetCommon(Params& p)
{
    p.a = 96;   p.b = 48;     // ceil(W/2)
    p.bond = 20000 * COIN;
    p.token = TOKEN_VALUE; p.ref = REF_WINDOW;
    p.arr[0] = 1; p.arr[1] = 2;  /* two on one line */ p.arr[2] = 3;
    p.req = true;
    p.carrier = BundleCarrier::SCRIPTSIG;
}
}
Params::Params()
    : startHeight(0), a(0), b(0), bond(0), token(0), ref(0), req(true), carrier(BundleCarrier::SCRIPTSIG)
{
    for (int i = 0; i < NUM_CLASSES; i++) {
        arr[i] = 0;
    }
}
const Params& MainParams()
{
    static Params p = [] {
        Params m;
        m.network = "main";
        SetCommon(m);
        m.addressVersion = { 0x1F, 0xE4 };
        m.startHeight = 3075000;
        return m;
    }();
    return p;
}
const Params& TestParams()
{
    static Params p = [] {
        Params t;
        t.network = "test";
        SetCommon(t);
        t.addressVersion = { 0x20, 0x07 };
        return t;
    }();
    return p;
}
Params RegtestParams(int startHeight, int sigmaRefBps, int supplyCapBps, int enforceUntil,
                     int attestArmMin, BundleCarrier bundleCarrier)
{
    Params r;
    r.network = "regtest";
    SetCommon(r);
    r.a = 8; r.b = 4;
    r.bond = 10 * COIN;
    r.startHeight = startHeight;
    r.carrier = bundleCarrier;
    return r;
}
"""


def test_strip_comments_keeps_strings():
    assert strip_comments('x = "a//b"; // c\ny /* z */ = 1;') == 'x = "a//b"; \ny   = 1;'


def test_constants_and_fields():
    consts = parse_constants(HEADER)
    assert consts == {"NUM_CLASSES": 3, "TOKEN_VALUE": 10000, "REF_WINDOW": 40}
    assert parse_struct_fields(HEADER, consts) == [
        "network", "startHeight", "addressVersion", "a", "b", "arr[0]", "arr[1]", "arr[2]",
        "bond", "token", "ref", "req", "carrier",
    ]


def test_extract_sources():
    ex = extract_sources(HEADER, CPP, ref="t", commit="c", regtest_flags={"startHeight": 7})
    m = ex.networks["main"]
    assert m["bond"] == 20000 * 100_000_000
    assert (m["a"], m["b"], m["token"], m["ref"]) == (96, 48, 10000, 40)
    assert [m[f"arr[{i}]"] for i in range(3)] == [1, 2, 3]
    assert m["addressVersion"] == "1FE4" and m["network"] == "main" and m["req"] is True
    assert m["carrier"] == "SCRIPTSIG"
    assert ex.networks["test"]["startHeight"] == 0
    r = ex.networks["regtest"]
    got = (r["a"], r["b"], r["bond"], r["startHeight"], r["carrier"])
    assert got == (8, 4, 10 * 100_000_000, 7, "SCRIPTSIG")
    assert ex.regtest_flags["attestArmMin"] == 3


@pytest.mark.parametrize(("expr", "want"), [
    ("20000 * COIN", 2_000_000_000_000), ("TOKEN_VALUE", 10000), ("0xFF", 255), ("-5", -5),
    ("7 / 2", 3), ("-7 / 2", -3), ("BundleCarrier::EITHER", "EITHER"), ("{ 0x20, 0x02 }", "2002"),
    ("true", True), ('"main"', "main"), ("100000000LL", 100000000),
])
def test_eval_expr(expr, want):
    assert eval_expr(expr, {"COIN": 100_000_000, "TOKEN_VALUE": 10000}) == want


def test_unknown_constructs_fail_loudly():
    with pytest.raises(ExtractError):
        eval_expr("UNKNOWN_CONST", {})
    with pytest.raises(ExtractError):
        extract_sources(HEADER, CPP.replace("p.req = true;", "p.req = Compute();"))
    with pytest.raises(ExtractError):
        extract_sources(HEADER, CPP.replace("p.req = true;", "p.nosuch = 1;"))


def test_check_drift_reports_each_kind(extracted):
    reg = dict(REGISTRY)
    del reg["grace"]
    reg["bogus"] = dataclasses.replace(REGISTRY["nReg"], name="bogus")
    reg["feeBps"] = dataclasses.replace(REGISTRY["feeBps"], mainnet=30)
    kinds = {(d.kind, d.name) for d in check_drift(reg, extracted)}
    assert ("missing-from-registry", "grace") in kinds
    assert ("extra-in-registry", "bogus") in kinds
    assert ("value-mismatch", "feeBps") in kinds


def test_type_mismatch_is_drift(extracted):
    reg = dict(REGISTRY)
    reg["attestRequired"] = dataclasses.replace(REGISTRY["attestRequired"], mainnet=1)
    assert any(d.name == "attestRequired" for d in check_drift(reg, extracted))
