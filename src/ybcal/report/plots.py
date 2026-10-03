"""matplotlib figures for the report (light/dark safe).

Owner: WP-8.

Figures are PNGs rendered on a light chart surface; the HTML report shows every figure on a light
card in both themes, so one rendering reads in light and dark mode. Colours are the validated
reference categorical palette (slot 1 blue, slot 2 orange) with text in neutral ink; one axis per
chart, thin marks, a legend whenever there are two series.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

OWNER_WP = "WP-8"

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
MUTED = "#a3a29c"


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "text.color": TEXT,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "font.size": 9,
            "legend.frameon": False,
            "lines.linewidth": 2,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
        }
    )
    return plt


def tornado(
    sens_tornado: Mapping[str, Mapping[str, Any]],
    base_value: float,
    metric_label: str,
    out: Path,
    *,
    top: int = 15,
    labels: Mapping[str, str] | None = None,
) -> Path | None:
    """Horizontal bars of the metric change at −1 / +1 step per parameter (largest first)."""
    rows = []
    for p, d in sens_tornado.items():
        lo = (d.get("y_lo") - base_value) if d.get("y_lo") is not None else 0.0
        hi = (d.get("y_hi") - base_value) if d.get("y_hi") is not None else 0.0
        if not (math.isfinite(lo) and math.isfinite(hi)):
            continue
        rows.append((p, lo, hi, max(abs(lo), abs(hi))))
    rows = [r for r in rows if r[3] > 0]
    if not rows:
        return None
    rows.sort(key=lambda r: -r[3])
    rows = rows[:top][::-1]
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.2, 0.32 * len(rows) + 1.3), dpi=110)
    y = range(len(rows))
    ax.barh(
        list(y),
        [r[1] for r in rows],
        height=0.36,
        color=SERIES[0],
        label="−1 step",
        align="edge",
        edgecolor=SURFACE,
        linewidth=1,
    )
    ax.barh(
        [i - 0.36 for i in y],
        [r[2] for r in rows],
        height=0.36,
        color=SERIES[1],
        label="+1 step",
        align="edge",
        edgecolor=SURFACE,
        linewidth=1,
    )
    ax.set_yticks([i for i in y])
    ax.set_yticklabels([(labels or {}).get(r[0], r[0]) for r in rows])
    ax.axvline(0, color=TEXT_2, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"change in {metric_label} from the recommended set ({base_value:.4g})")
    ax.set_title(f"Tornado: {metric_label}")
    ax.legend(loc="lower right", ncols=2)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def sobol_bars(
    indices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    metric_labels: Mapping[str, str],
    out: Path,
    *,
    key: str = "ST",
    threshold: float | None = None,
    top: int = 10,
) -> Path | None:
    """Small multiples: one panel per metric, the factors' ``key`` index as horizontal bars."""
    mets = [m for m in indices if indices[m]]
    if not mets:
        return None
    plt = _plt()
    ncol = min(2, len(mets))
    nrow = math.ceil(len(mets) / ncol)
    nf = min(top, max(len(v) for v in indices.values()))
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.2, (0.26 * nf + 1.1) * nrow), dpi=110, squeeze=False)
    for i, m in enumerate(mets):
        ax = axes[i // ncol][i % ncol]
        items = sorted(
            ((f, float(d.get(key, 0.0) or 0.0)) for f, d in indices[m].items()), key=lambda t: -t[1]
        )
        items = items[:top][::-1]
        vals = [max(0.0, v) for _, v in items]
        ax.barh(range(len(items)), vals, height=0.6, color=SERIES[0], edgecolor=SURFACE, linewidth=1)
        ax.set_yticks(range(len(items)))
        ax.set_yticklabels([f for f, _ in items], fontsize=8)
        if threshold is not None:
            ax.axvline(threshold, color=MUTED, linewidth=1, linestyle="--")
        ax.grid(axis="y", visible=False)
        ax.set_title(metric_labels.get(m, m), fontsize=9)
        ax.set_xlim(0, max(1.0, max(vals or [0]) * 1.05))
    for j in range(len(mets), nrow * ncol):
        axes[j // ncol][j % ncol].axis("off")
    fig.suptitle(
        f"Total-order index ({key}) by factor"
        + (f"; dashed = insensitive threshold {threshold:g}" if threshold is not None else ""),
        x=0.01,
        ha="left",
        fontsize=10,
        fontweight="bold",
    )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def neighbour_plot(
    param: str,
    current: float,
    recommended: float,
    points: Sequence[Mapping[str, Any]],
    rec_primary: float | None,
    metric_label: str,
    out: Path,
) -> Path | None:
    """The primary metric at the recommended value and its ±k-step neighbours (for parameters whose
    study produced no figure of its own)."""
    xs, ys, feas = [], [], []
    for q in points:
        if q.get("primary") is None:
            continue
        xs.append(float(q["value"]))
        ys.append(float(q["primary"]))
        feas.append(bool(q.get("feasible", True)))
    if rec_primary is not None and math.isfinite(rec_primary):
        xs.append(float(recommended))
        ys.append(float(rec_primary))
        feas.append(True)
    if len(xs) < 2:
        return None
    order = sorted(range(len(xs)), key=xs.__getitem__)
    xs = [xs[i] for i in order]
    ys = [ys[i] for i in order]
    feas = [feas[i] for i in order]
    plt = _plt()
    fig, ax = plt.subplots(figsize=(5.6, 2.6), dpi=110)
    ax.plot(xs, ys, color=SERIES[0], marker="o", markersize=5, markeredgecolor=SURFACE)
    for x, y, f in zip(xs, ys, feas, strict=True):
        if not f:
            ax.plot([x], [y], marker="x", color=SERIES[1], markersize=8, linestyle="none")
    ax.axvline(float(recommended), color=SERIES[0], linewidth=1, linestyle="--")
    if current != recommended:
        ax.axvline(float(current), color=TEXT_2, linewidth=1, linestyle=":")
    ax.set_xlabel(
        f"{param}  (dashed: recommended"
        + ("; dotted: current" if current != recommended else "")
        + "; × violates the policy)"
    )
    ax.set_ylabel(metric_label)
    ax.set_title(f"{param}: neighbours of the recommended value")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
