"""Matplotlib figure builders shared by the dashboard (consistent styling,
None-tolerant)."""

from __future__ import annotations

import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

warnings.filterwarnings("ignore", message="Tight layout not applied")
warnings.filterwarnings("ignore", message="This figure includes Axes that are not compatible")

PRIMARY = "#1b5e20"
ACCENT = ["#1b5e20", "#4a9b3f", "#f1c40f", "#e67e22", "#c0392b", "#2e7d32", "#7f8c8d"]
GRID = "#dddddd"


def _clean(vals):
    return np.array([np.nan if v is None else float(v) for v in vals], dtype=float)


def _style(ax):
    ax.grid(True, axis="y", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def revenue_profit_trend(periods, revenue, ebitda_margin, currency=""):
    fig, ax1 = plt.subplots(figsize=(6.4, 3.4))
    x = np.arange(len(periods))
    ax1.bar(x, _clean(revenue), color=PRIMARY, alpha=0.85, label="Revenue")
    ax1.set_xticks(x, periods)
    ax1.set_ylabel(f"Revenue ({currency})" if currency else "Revenue")
    _style(ax1)
    ax2 = ax1.twinx()
    ax2.plot(x, _clean(ebitda_margin), color="#e67e22", marker="o", lw=2, label="EBITDA margin %")
    ax2.set_ylabel("EBITDA margin %")
    ax2.spines["top"].set_visible(False)
    lines = ax1.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labels = ax1.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax1.legend(lines, labels, loc="upper left", fontsize=8, frameon=False)
    fig.tight_layout()
    return fig


def multi_line(periods, series: dict, title="", ylabel="", marker="o"):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    x = np.arange(len(periods))
    for i, (name, vals) in enumerate(series.items()):
        ax.plot(x, _clean(vals), marker=marker, lw=2, color=ACCENT[i % len(ACCENT)], label=name)
    ax.set_xticks(x, periods)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    _style(ax)
    fig.tight_layout()
    return fig


def stacked_bars(periods, series: dict, title="", ylabel=""):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    x = np.arange(len(periods))
    bottom = np.zeros(len(periods))
    for i, (name, vals) in enumerate(series.items()):
        v = np.nan_to_num(_clean(vals))
        ax.bar(x, v, bottom=bottom, color=ACCENT[i % len(ACCENT)], label=name)
        bottom += v
    ax.set_xticks(x, periods)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    _style(ax)
    fig.tight_layout()
    return fig


def grouped_bars(periods, series: dict, title="", ylabel=""):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    n = max(len(series), 1)
    x = np.arange(len(periods))
    w = 0.8 / n
    for i, (name, vals) in enumerate(series.items()):
        ax.bar(x + (i - (n - 1) / 2) * w, np.nan_to_num(_clean(vals)), w,
               color=ACCENT[i % len(ACCENT)], label=name)
    ax.set_xticks(x, periods)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.axhline(0, color="#888", lw=0.8)
    _style(ax)
    fig.tight_layout()
    return fig


def waterfall(steps: list[tuple[str, float]], title=""):
    """steps: ordered (label, delta-or-level). First and any label starting with
    '=' are treated as absolute levels; others as deltas."""
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    running = 0.0
    for i, (label, val) in enumerate(steps):
        absolute = i == 0 or label.startswith("=")
        val = 0.0 if val is None else float(val)
        if absolute:
            ax.bar(i, val, color=PRIMARY)
            running = val
        else:
            color = ACCENT[1] if val >= 0 else ACCENT[4]
            ax.bar(i, val, bottom=running, color=color)
            running += val
    ax.set_xticks(range(len(steps)), [s[0].lstrip("=") for s in steps], rotation=20, ha="right", fontsize=8)
    if title:
        ax.set_title(title, fontsize=10)
    _style(ax)
    fig.tight_layout()
    return fig


def donut(labels, values, title=""):
    vals = [max(0.0, 0.0 if v is None else float(v)) for v in values]
    pairs = [(l, v) for l, v in zip(labels, vals) if v > 0]
    fig, ax = plt.subplots(figsize=(4.2, 3.6))
    if pairs:
        ls, vs = zip(*pairs)
        ax.pie(vs, labels=ls, autopct="%1.0f%%", pctdistance=0.8, startangle=90,
               colors=[ACCENT[i % len(ACCENT)] for i in range(len(vs))],
               wedgeprops=dict(width=0.42), textprops={"fontsize": 8})
    if title:
        ax.set_title(title, fontsize=10)
    fig.tight_layout()
    return fig


def score_trend(periods, scores, bands: list[tuple[float, str, str]]):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    x = np.arange(len(periods))
    edges = sorted([b[0] for b in bands] + [100])
    fills = ["#f8d0cc", "#fbe6c9", "#fdf3cf", "#e8f3d6", "#d6ecd6"]
    for i in range(len(edges) - 1):
        ax.axhspan(edges[i], edges[i + 1], color=fills[min(i, len(fills) - 1)], alpha=0.5)
    ax.axhline(64, color="#888", lw=1, ls="--")
    ax.text(0, 65, "investment grade", fontsize=7, color="#555")
    ax.plot(x, _clean(scores), marker="o", lw=2.5, color=PRIMARY)
    ax.set_xticks(x, periods)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Composite score")
    _style(ax)
    fig.tight_layout()
    return fig


def status_heatmap(df_num, df_txt, lower_better_rows: set[str] | None = None):
    """df_num/df_txt: index = ratio label, columns = periods."""
    lower_better_rows = lower_better_rows or set()
    fig, ax = plt.subplots(figsize=(1.6 + 0.9 * df_num.shape[1], 0.45 * df_num.shape[0] + 1))
    norm = df_num.copy().astype(float)
    for r in norm.index:
        row = norm.loc[r]
        lo, hi = np.nanmin(row.values), np.nanmax(row.values)
        rng = (hi - lo) or 1.0
        scaled = (row - lo) / rng
        if r in lower_better_rows:
            scaled = 1 - scaled
        norm.loc[r] = scaled
    ax.imshow(norm.values, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(df_num.shape[1]), df_num.columns)
    ax.set_yticks(range(df_num.shape[0]), df_num.index, fontsize=8)
    for i in range(df_num.shape[0]):
        for j in range(df_num.shape[1]):
            ax.text(j, i, df_txt.iat[i, j], ha="center", va="center", fontsize=8)
    fig.tight_layout()
    return fig
