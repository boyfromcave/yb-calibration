"""Scrape a devnet node into per-block records: ``yed_gethistory`` + state RPCs → CSV / JSON.

Owner: WP-9.

Pulls ``yed_gethistory`` (in chunks of at most 2,016 rows, the RPC's limit), ``yed_getstats``,
``yed_getinfo``, ``yed_listvaults`` (paged), ``yed_getactivation`` and ``yed_listattestors``
(v3; recorded as an error on a node without it) and normalises them into records with typed
integer fields. Field names are those of ``doc/yellowback-rpc.md`` at the pin; the additions are
``activationStatus``/``lockInHeight``/``activateHeight`` (the flattened ``activation`` object),
``activationCode`` (0 signaling, 1 locked_in, 2 active), ``haltMask`` as the §3.6 bitmask integer
and ``haltNames`` (the decoded names joined by ``|``). Undefined prices / ratio stay ``None``
(empty in CSV). The same normalisation is what the simulator's records are compared in
(:mod:`ybcal.devnet.diff`).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OWNER_WP = "WP-9"

#: ``yed_gethistory`` row limit per call.
HISTORY_CHUNK = 2016
#: ``yed_listvaults`` page size.
VAULT_PAGE = 100

#: §3.6 haltMask bits, declaration order (view.h, yellowback_model.py HALT_NAMES).
HALT_BITS: dict[str, int] = {
    "NOT_ACTIVE": 1 << 0,
    "NO_PRICE": 1 << 1,
    "PARTICIPATION": 1 << 2,
    "GLOBAL_RATIO": 1 << 3,
    "DIVERGENCE": 1 << 4,
    "ENFORCEMENT": 1 << 5,
}
#: Activation status → code.
ACTIVATION_CODES: dict[str, int] = {"signaling": 0, "locked_in": 1, "active": 2}

#: Per-block record fields, in CSV column order.
HISTORY_FIELDS: tuple[str, ...] = (
    "height",
    "blockHash",
    "tagged",
    "quote",
    "signalCount",
    "activationStatus",
    "activationCode",
    "lockInHeight",
    "activateHeight",
    "pFast",
    "pMid",
    "pSlow",
    "pMint",
    "pClaim",
    "sigmaMultBps",
    "issuedZat",
    "supplyCents",
    "collateralZat",
    "globalRatioBps",
    "haltMask",
    "haltNames",
)
#: Integer fields that may be ``None`` (undefined, rendered ``null`` by the node).
NULLABLE_INT: frozenset[str] = frozenset({"pFast", "pMid", "pSlow", "pMint", "pClaim", "globalRatioBps"})
_REQUIRED_INT = ("height", "signalCount", "sigmaMultBps", "issuedZat", "supplyCents", "collateralZat")

#: ``yed_listvaults`` record fields kept (``collateral``, the decimal twin, is dropped).
VAULT_FIELDS: tuple[str, ...] = (
    "txid",
    "vout",
    "status",
    "termClass",
    "lockHeight",
    "claimHeight",
    "collateralZat",
    "mintedCents",
    "mintHeight",
    "refHeight",
    "feePaidZat",
    "closeHeight",
    "closingTxid",
    "burnedCents",
    "unbacked",
    "claimable",
    "underwaterAt",
    "voidReason",
    "sweepBefore",
    "noticed",
    "noticeHeight",
    "emergencyOpenAt",
    "ownerAddress",
)
_VAULT_INT = (
    "vout",
    "lockHeight",
    "claimHeight",
    "collateralZat",
    "mintedCents",
    "mintHeight",
    "refHeight",
    "feePaidZat",
    "burnedCents",
)
_VAULT_NULLABLE_INT = ("closeHeight", "underwaterAt", "sweepBefore", "noticeHeight", "emergencyOpenAt")
_VAULT_BOOL = ("unbacked", "claimable", "noticed")


class ScrapeError(ValueError):
    """A node answer that does not have the documented shape."""


def _int(v: Any, name: str, nullable: bool = False) -> int | None:
    if v is None:
        if nullable:
            return None
        raise ScrapeError(f"{name} is null")
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, str) and v.lstrip("-").isdigit():
            return int(v)
        raise ScrapeError(f"{name} must be an integer, got {v!r}")
    return v


def halt_mask(names: Iterable[str]) -> int:
    """Decoded halt names → the §3.6 bitmask."""
    m = 0
    for n in names:
        if n not in HALT_BITS:
            raise ScrapeError(f"unknown halt name {n!r}")
        m |= HALT_BITS[n]
    return m


def halt_names(mask: int) -> list[str]:
    """Bitmask → names in declaration order."""
    return [n for n, b in HALT_BITS.items() if mask & b]


def normalize_history_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``yed_gethistory`` row → a typed per-block record (:data:`HISTORY_FIELDS`)."""
    act = row.get("activation") or {}
    status = act.get("status", "")
    if status not in ACTIVATION_CODES:
        raise ScrapeError(f"unknown activation status {status!r} at height {row.get('height')}")
    names = list(row.get("haltMask") or [])
    rec: dict[str, Any] = {
        "height": _int(row.get("height"), "height"),
        "blockHash": str(row.get("blockHash", "")),
        "tagged": int(bool(row.get("tagged"))),
        "quote": int(bool(row.get("quote"))),
        "signalCount": _int(row.get("signalCount"), "signalCount"),
        "activationStatus": status,
        "activationCode": ACTIVATION_CODES[status],
        "lockInHeight": _int(act.get("lockInHeight", 0), "lockInHeight"),
        "activateHeight": _int(act.get("activateHeight", 0), "activateHeight"),
    }
    for k in ("pFast", "pMid", "pSlow", "pMint", "pClaim"):
        rec[k] = _int(row.get(k), k, nullable=True)
    for k in ("sigmaMultBps", "issuedZat", "supplyCents", "collateralZat"):
        rec[k] = _int(row.get(k), k)
    rec["globalRatioBps"] = _int(row.get("globalRatioBps"), "globalRatioBps", nullable=True)
    rec["haltMask"] = halt_mask(names)
    rec["haltNames"] = "|".join(halt_names(rec["haltMask"]))
    return rec


def normalize_vault(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``yed_listvaults`` row → a typed record keyed ``vault = "<txid>:<vout>"``."""
    rec: dict[str, Any] = {}
    for k in _VAULT_INT:
        rec[k] = _int(row.get(k), k)
    for k in _VAULT_NULLABLE_INT:
        rec[k] = _int(row.get(k), k, nullable=True)
    for k in _VAULT_BOOL:
        rec[k] = int(bool(row.get(k)))
    for k in ("txid", "status", "termClass", "closingTxid", "voidReason", "ownerAddress"):
        rec[k] = str(row.get(k) or "")
    rec["vault"] = f"{rec['txid']}:{rec['vout']}"
    return {"vault": rec["vault"], **{k: rec[k] for k in VAULT_FIELDS}}


def normalize_attestor(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``yed_listattestors`` row → typed (``weight`` decimal string → int, flags flattened)."""
    flags = row.get("flags") or {}
    out = {
        "seq": _int(row.get("seq"), "seq"),
        "status": str(row.get("status", "")),
        "statusHeight": _int(row.get("statusHeight", 0), "statusHeight"),
        "registerHeight": _int(row.get("registerHeight", 0), "registerHeight"),
        "bondZat": _int(row.get("bondZat", 0), "bondZat"),
        "bondLocktime": _int(row.get("bondLocktime", 0), "bondLocktime"),
        "weight": int(str(row.get("weight", "0"))),
        "seated": int(bool(row.get("seated"))),
        "pinned": int(bool(row.get("pinned"))),
        "founding": int(bool(row.get("founding"))),
        "seatedSince": _int(row.get("seatedSince"), "seatedSince", nullable=True),
        "lastBundleHeight": _int(row.get("lastBundleHeight"), "lastBundleHeight", nullable=True),
        "bondSpentHeight": _int(row.get("bondSpentHeight"), "bondSpentHeight", nullable=True),
        "poolFresh": int(bool(row.get("poolFresh"))),
        "tier": _int(flags.get("tier", 0), "tier"),
        "pool": int(bool(flags.get("pool"))),
        "attestorPubKey": str(row.get("attestorPubKey", "")),
    }
    return out


def fetch_history(client: Any, start: int, end: int, chunk: int = HISTORY_CHUNK) -> list[dict[str, Any]]:
    """Raw ``yed_gethistory`` rows for ``start ≤ h ≤ end`` in calls of at most ``chunk`` rows."""
    if chunk < 1 or chunk > HISTORY_CHUNK:
        raise ValueError(f"chunk must be in [1, {HISTORY_CHUNK}]")
    rows: list[dict[str, Any]] = []
    h = start
    while h <= end:
        to = min(end, h + chunk - 1)
        part = client.call("yed_gethistory", h, to)
        if len(part) != to - h + 1:
            raise ScrapeError(f"yed_gethistory {h} {to} returned {len(part)} rows, expected {to - h + 1}")
        rows.extend(part)
        h = to + 1
    return rows


def fetch_vaults(client: Any, page: int = VAULT_PAGE) -> list[dict[str, Any]]:
    """Every vault (all statuses), paging ``yed_listvaults "" count skip``."""
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        part = client.call("yed_listvaults", "", page, skip)
        out.extend(part)
        if len(part) < page:
            return out
        skip += page


@dataclass
class ScrapeResult:
    """Everything one scrape read, normalised."""

    history: list[dict[str, Any]]
    info: dict[str, Any]
    stats: dict[str, Any]
    activation: dict[str, Any]
    vaults: list[dict[str, Any]]
    attestors: list[dict[str, Any]] | None
    errors: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    @property
    def heights(self) -> tuple[int, int] | None:
        """First and last height scraped."""
        return (self.history[0]["height"], self.history[-1]["height"]) if self.history else None


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    """CSV with ``None`` as an empty cell."""
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def load_history_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read a ``history.csv`` back into typed records (inverse of the scrape's writer)."""
    out = []
    with Path(path).open(newline="") as fh:
        for row in csv.DictReader(fh):
            rec: dict[str, Any] = {}
            for k in HISTORY_FIELDS:
                v = row.get(k, "")
                if k in ("blockHash", "activationStatus", "haltNames"):
                    rec[k] = v
                elif v == "":
                    rec[k] = None
                else:
                    rec[k] = int(v)
            out.append(rec)
    return out


def scrape(
    client: Any,
    out_dir: str | Path | None = None,
    *,
    from_height: int | None = None,
    to_height: int | None = None,
) -> ScrapeResult:
    """Scrape one node; write ``history.{csv,json}``, ``vaults.{csv,json}``, ``info.json``,
    ``stats.json``, ``activation.json``, ``attestors.json`` and ``scrape.json`` into ``out_dir``."""
    errors: dict[str, str] = {}
    info = client.call("yed_getinfo")
    start = max(int(info.get("startHeight") or 1), from_height or 0)
    tip = int(info.get("height", -1)) if to_height is None else to_height
    raw = fetch_history(client, start, tip) if tip >= start else []
    history = [normalize_history_row(r) for r in raw]
    stats = client.call("yed_getstats")
    activation = client.call("yed_getactivation")
    vaults = [normalize_vault(v) for v in fetch_vaults(client)]
    attestors: list[dict[str, Any]] | None
    try:
        attestors = [normalize_attestor(a) for a in client.call("yed_listattestors")]
    except Exception as e:  # a v2 node has no yed_listattestors
        attestors = None
        errors["yed_listattestors"] = str(e)
    res = ScrapeResult(history, info, stats, activation, vaults, attestors, errors)
    if out_dir is not None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        write_csv(d / "history.csv", history, HISTORY_FIELDS)
        write_csv(d / "vaults.csv", vaults, ("vault", *VAULT_FIELDS))
        docs = {
            "history.json": history,
            "vaults.json": vaults,
            "info.json": info,
            "stats.json": stats,
            "activation.json": activation,
            "attestors.json": attestors,
            "scrape.json": {
                "format": "ybcal-scrape/1",
                "from": start,
                "to": tip,
                "rows": len(history),
                "errors": errors,
            },
        }
        for name, doc in docs.items():
            (d / name).write_text(json.dumps(doc, indent=2) + "\n")
        res.files = {n: d / n for n in ("history.csv", "vaults.csv", *docs)}
    return res
