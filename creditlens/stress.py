"""Scenario stress-testing engine.

Takes a derived values dict, applies a set of shocks, cascades them through the
P&L / cash flow in a deliberately simple way (constant tax rate, D&A held flat,
cash balance held flat), and re-derives a full values dict so the ratio and
scoring engines can run unchanged on the stressed figures.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .model import derive
from .ratios import RATIO_BY_KEY, compute_all
from .scoring import score_credit


@dataclass
class StressParams:
    revenue_decline_pct: float = 0.0        # % fall in revenue
    ebitda_decline_pct: float = 0.0         # % fall applied directly to EBITDA
    ebitda_margin_drop_pp: float = 0.0      # extra margin compression, percentage points
    rate_shock_bps: float = 0.0            # parallel rate shock on floating-rate debt
    floating_debt_pct: float = 100.0       # share of debt that reprices
    capex_change_pct: float = 0.0          # % change in capex
    incremental_debt: float = 0.0          # new debt drawn (same units as inputs)

    def is_base(self) -> bool:
        return (self.revenue_decline_pct == 0 and self.ebitda_decline_pct == 0
                and self.ebitda_margin_drop_pp == 0 and self.rate_shock_bps == 0
                and self.capex_change_pct == 0 and self.incremental_debt == 0)


def apply_stress(base: dict, p: StressParams) -> dict:
    if p.is_base():
        return base
    v = base
    rev0 = v.get("revenue") or 0.0
    ebitda0 = v.get("ebitda") or 0.0
    da = v.get("depreciation_amortization") or 0.0
    int0 = v.get("interest_expense") or 0.0
    debt0 = v.get("total_debt") or 0.0
    ni0 = v.get("net_income") or 0.0
    cfo0 = v.get("cfo") or 0.0
    capex0 = v.get("capex") or 0.0
    tax_rate = v.get("_tax_rate", 0.25)

    # --- revenue & EBITDA ---
    rev1 = rev0 * (1 - p.revenue_decline_pct / 100.0)
    margin0 = (ebitda0 / rev0) if rev0 else 0.0
    margin1 = margin0 - p.ebitda_margin_drop_pp / 100.0
    ebitda1 = rev1 * margin1 * (1 - p.ebitda_decline_pct / 100.0)
    ebit1 = ebitda1 - da

    # --- interest ---
    avg_cost = (int0 / debt0) if debt0 else 0.06
    float_debt = debt0 * p.floating_debt_pct / 100.0
    int1 = int0 + float_debt * p.rate_shock_bps / 10000.0
    int1 += p.incremental_debt * (avg_cost + p.rate_shock_bps / 10000.0)
    debt1 = debt0 + p.incremental_debt
    ltd1 = (v.get("long_term_debt") or 0.0) + p.incremental_debt

    # --- earnings ---
    pretax1 = ebit1 - int1 + (v.get("interest_income") or 0.0)
    tax1 = max(pretax1, 0.0) * tax_rate
    ni1 = pretax1 - tax1

    # --- cash flow (delta from base, after tax) ---
    capex1 = capex0 * (1 + p.capex_change_pct / 100.0)
    d_ebitda = ebitda1 - ebitda0
    d_int = int1 - int0
    cfo1 = cfo0 + (d_ebitda - d_int) * (1 - tax_rate)

    # --- balance sheet roll-forward (approximate) ---
    d_ni = ni1 - ni0
    equity1 = (v.get("total_equity") or 0.0) + d_ni
    assets1 = (v.get("total_assets") or 0.0) + p.incremental_debt + d_ni
    liabilities1 = (v.get("total_liabilities") or 0.0) + p.incremental_debt

    gp_ratio = (v.get("gross_profit") / rev0) if (v.get("gross_profit") and rev0) else None
    gross_profit1 = rev1 * gp_ratio if gp_ratio is not None else None

    stressed_raw = {
        "revenue": rev1,
        "gross_profit": gross_profit1,
        "cogs": (rev1 - gross_profit1) if gross_profit1 is not None else v.get("cogs"),
        "sga": v.get("sga"),
        "depreciation_amortization": da,
        "ebitda": ebitda1,
        "ebit": ebit1,
        "interest_expense": int1,
        "interest_income": v.get("interest_income"),
        "pretax_income": pretax1,
        "tax_expense": tax1,
        "net_income": ni1,
        "cash": v.get("cash"),
        "short_term_investments": v.get("short_term_investments"),
        "accounts_receivable": v.get("accounts_receivable"),
        "inventory": v.get("inventory"),
        "other_current_assets": v.get("other_current_assets"),
        "current_assets": v.get("current_assets"),
        "net_ppe": v.get("net_ppe"),
        "total_assets": assets1,
        "accounts_payable": v.get("accounts_payable"),
        "short_term_debt": v.get("short_term_debt"),
        "other_current_liabilities": v.get("other_current_liabilities"),
        "current_liabilities": v.get("current_liabilities"),
        "long_term_debt": ltd1,
        "other_liabilities": v.get("other_liabilities"),
        "total_liabilities": liabilities1,
        "total_equity": equity1,
        "cfo": cfo1,
        "capex": capex1,
        "dividends_paid": v.get("dividends_paid"),
        "interest_paid": int1,
    }
    return derive(stressed_raw)


def stress_summary(base: dict, p: StressParams) -> dict:
    sv = apply_stress(base, p)
    r = compute_all(sv)
    sc = score_credit(r)
    return {"values": sv, "ratios": r, "score": sc}


_HEATMAP_METRICS = {
    "Composite score": ("score", None),
    "Indicative rating": ("rating", None),
    "Net debt / EBITDA": ("ratio", "net_debt_ebitda"),
    "EBITDA / Interest": ("ratio", "ebitda_interest"),
    "FFO / Total debt (%)": ("ratio", "ffo_debt"),
    "FCF / Total debt (%)": ("ratio", "fcf_debt"),
}

HEATMAP_METRIC_NAMES = list(_HEATMAP_METRICS)


def heatmap_frame(
    base: dict,
    metric: str,
    ebitda_declines: list[float],
    rate_shocks: list[float],
    floating_debt_pct: float = 100.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (numeric frame for colouring, text frame for annotation).

    Rows are EBITDA decline %, columns are rate shock in bps.
    """
    kind, ratio_key = _HEATMAP_METRICS[metric]
    rows_num, rows_txt = [], []
    for ed in ebitda_declines:
        num_row, txt_row = [], []
        for rs in rate_shocks:
            res = stress_summary(base, StressParams(
                ebitda_decline_pct=ed, rate_shock_bps=rs,
                floating_debt_pct=floating_debt_pct))
            if kind == "score":
                num_row.append(res["score"].composite)
                txt_row.append(f"{res['score'].composite:.0f}")
            elif kind == "rating":
                num_row.append(res["score"].composite)
                txt_row.append(res["score"].rating)
            else:
                val = res["ratios"].get(ratio_key)
                rdef = RATIO_BY_KEY[ratio_key]
                num_row.append(np.nan if val is None else val)
                txt_row.append("n/m" if val is None
                               else (f"{val:.2f}" if rdef.unit == "x" else f"{val:.0f}"))
        rows_num.append(num_row)
        rows_txt.append(txt_row)

    idx = [f"-{ed:.0f}%" for ed in ebitda_declines]
    cols = [f"+{rs:.0f}" for rs in rate_shocks]
    return (pd.DataFrame(rows_num, index=idx, columns=cols),
            pd.DataFrame(rows_txt, index=idx, columns=cols))


def metric_lower_is_better(metric: str) -> bool:
    _, ratio_key = _HEATMAP_METRICS[metric]
    if ratio_key is None:
        return False
    return RATIO_BY_KEY[ratio_key].direction == "lower"
