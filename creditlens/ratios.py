"""Ratio catalogue grouped by analytical category.

Every ratio is a :class:`RatioDef` with a pure ``func`` that takes the derived
values dict (see :func:`creditlens.model.derive`) and returns a float or ``None``
when the ratio is not meaningful (zero / missing denominator).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

Number = Optional[float]

CATEGORIES = [
    "Profitability",
    "Leverage",
    "Coverage",
    "Liquidity",
    "Solvency",
    "Cash Flow",
]


def sdiv(a: Number, b: Number) -> Number:
    if a is None or b is None or b == 0:
        return None
    return a / b


@dataclass(frozen=True)
class RatioDef:
    key: str
    label: str
    category: str
    unit: str              # "%" | "x" | "ratio" | "amount"
    direction: str         # "higher" | "lower" | "neutral"
    func: Callable[[dict], Number]
    good: Number = None     # >= good (higher) / <= good (lower) reads as strong
    weak: Number = None     # <= weak (higher) / >= weak (lower) reads as pressured
    note: str = ""

    def compute(self, v: dict) -> Number:
        try:
            return self.func(v)
        except (TypeError, ZeroDivisionError):
            return None

    def status(self, value: Number) -> str:
        """One of 'strong' | 'adequate' | 'weak' | 'na' for display colouring."""
        if value is None or self.good is None or self.weak is None:
            return "na"
        if self.direction == "higher":
            if value >= self.good:
                return "strong"
            if value <= self.weak:
                return "weak"
        elif self.direction == "lower":
            if value <= self.good:
                return "strong"
            if value >= self.weak:
                return "weak"
        else:
            return "adequate"
        return "adequate"


def _pct(a: Number, b: Number) -> Number:
    r = sdiv(a, b)
    return None if r is None else r * 100.0


RATIOS: list[RatioDef] = [
    # ---------------- Profitability ----------------
    RatioDef("gross_margin", "Gross margin", "Profitability", "%", "higher",
             lambda v: _pct(v.get("gross_profit"), v.get("revenue")), good=40, weak=20),
    RatioDef("ebitda_margin", "EBITDA margin", "Profitability", "%", "higher",
             lambda v: _pct(v.get("ebitda"), v.get("revenue")), good=25, weak=8),
    RatioDef("ebit_margin", "EBIT margin", "Profitability", "%", "higher",
             lambda v: _pct(v.get("ebit"), v.get("revenue")), good=18, weak=4),
    RatioDef("net_margin", "Net profit margin", "Profitability", "%", "higher",
             lambda v: _pct(v.get("net_income"), v.get("revenue")), good=12, weak=2),
    RatioDef("roa", "Return on assets", "Profitability", "%", "higher",
             lambda v: _pct(v.get("net_income"), v.get("total_assets")), good=8, weak=1),
    RatioDef("roe", "Return on equity", "Profitability", "%", "higher",
             lambda v: _pct(v.get("net_income"), v.get("total_equity")), good=15, weak=3),
    RatioDef("roce", "Return on capital employed", "Profitability", "%", "higher",
             lambda v: _pct(v.get("ebit"), v.get("capital_employed")), good=15, weak=4),
    # ---------------- Leverage ----------------
    RatioDef("debt_ebitda", "Total debt / EBITDA", "Leverage", "x", "lower",
             lambda v: sdiv(v.get("total_debt"), v.get("ebitda")), good=2.0, weak=4.5),
    RatioDef("net_debt_ebitda", "Net debt / EBITDA", "Leverage", "x", "lower",
             lambda v: sdiv(v.get("net_debt"), v.get("ebitda")), good=1.5, weak=4.0),
    RatioDef("debt_equity", "Total debt / Equity", "Leverage", "x", "lower",
             lambda v: sdiv(v.get("total_debt"), v.get("total_equity")), good=0.6, weak=2.0),
    RatioDef("debt_capital", "Total debt / (Debt + Equity)", "Leverage", "%", "lower",
             lambda v: _pct(v.get("total_debt"), v.get("invested_capital")), good=30, weak=65),
    RatioDef("debt_assets", "Total debt / Total assets", "Leverage", "%", "lower",
             lambda v: _pct(v.get("total_debt"), v.get("total_assets")), good=25, weak=55),
    RatioDef("financial_leverage", "Assets / Equity (financial leverage)", "Leverage", "x", "lower",
             lambda v: sdiv(v.get("total_assets"), v.get("total_equity")), good=2.0, weak=5.0),
    # ---------------- Coverage ----------------
    RatioDef("ebitda_interest", "EBITDA / Interest", "Coverage", "x", "higher",
             lambda v: sdiv(v.get("ebitda"), v.get("interest_expense")), good=6.0, weak=2.0),
    RatioDef("ebit_interest", "EBIT / Interest (times interest earned)", "Coverage", "x", "higher",
             lambda v: sdiv(v.get("ebit"), v.get("interest_expense")), good=4.0, weak=1.5),
    RatioDef("ebitda_capex_interest", "(EBITDA - Capex) / Interest", "Coverage", "x", "higher",
             lambda v: sdiv((None if v.get("ebitda") is None or v.get("capex") is None
                             else v["ebitda"] - v["capex"]), v.get("interest_expense")),
             good=4.0, weak=1.0),
    RatioDef("ffo_debt", "FFO / Total debt", "Coverage", "%", "higher",
             lambda v: _pct(v.get("ffo"), v.get("total_debt")), good=35, weak=12),
    RatioDef("cfo_debt", "CFO / Total debt", "Coverage", "%", "higher",
             lambda v: _pct(v.get("cfo"), v.get("total_debt")), good=30, weak=10),
    RatioDef("dscr", "Debt-service coverage ((EBITDA - Capex) / (Int + ST debt))",
             "Coverage", "x", "higher",
             lambda v: sdiv((None if v.get("ebitda") is None or v.get("capex") is None
                             else v["ebitda"] - v["capex"]),
                            (None if v.get("interest_paid") is None and v.get("short_term_debt") is None
                             else (v.get("interest_paid") or 0) + (v.get("short_term_debt") or 0))),
             good=1.5, weak=1.0),
    # ---------------- Liquidity ----------------
    RatioDef("current_ratio", "Current ratio", "Liquidity", "x", "higher",
             lambda v: sdiv(v.get("current_assets"), v.get("current_liabilities")), good=1.8, weak=1.0),
    RatioDef("quick_ratio", "Quick ratio", "Liquidity", "x", "higher",
             lambda v: sdiv((None if v.get("current_assets") is None
                             else v["current_assets"] - (v.get("inventory") or 0)),
                            v.get("current_liabilities")), good=1.2, weak=0.7),
    RatioDef("cash_ratio", "Cash ratio", "Liquidity", "x", "higher",
             lambda v: sdiv(v.get("_cash_like"), v.get("current_liabilities")), good=0.5, weak=0.15),
    RatioDef("working_capital", "Working capital", "Liquidity", "amount", "higher",
             lambda v: v.get("working_capital")),
    RatioDef("wc_revenue", "Working capital / Revenue", "Liquidity", "%", "neutral",
             lambda v: _pct(v.get("working_capital"), v.get("revenue"))),
    # ---------------- Solvency ----------------
    RatioDef("equity_ratio", "Equity / Total assets", "Solvency", "%", "higher",
             lambda v: _pct(v.get("total_equity"), v.get("total_assets")), good=45, weak=20),
    RatioDef("liabilities_assets", "Total liabilities / Total assets", "Solvency", "%", "lower",
             lambda v: _pct(v.get("total_liabilities"), v.get("total_assets")), good=50, weak=80),
    RatioDef("liabilities_equity", "Total liabilities / Equity", "Solvency", "x", "lower",
             lambda v: sdiv(v.get("total_liabilities"), v.get("total_equity")), good=1.0, weak=4.0),
    RatioDef("net_debt_capital", "Net debt / (Net debt + Equity)", "Solvency", "%", "lower",
             lambda v: _pct(v.get("net_debt"),
                            (None if v.get("total_equity") is None or v.get("net_debt") is None
                             else v["net_debt"] + v["total_equity"])), good=25, weak=60),
    RatioDef("net_debt_equity", "Net debt / Equity", "Solvency", "x", "lower",
             lambda v: sdiv(v.get("net_debt"), v.get("total_equity")), good=0.4, weak=2.0),
    # ---------------- Cash Flow ----------------
    RatioDef("cfo_margin", "CFO / Revenue", "Cash Flow", "%", "higher",
             lambda v: _pct(v.get("cfo"), v.get("revenue")), good=15, weak=4),
    RatioDef("fcf_margin", "FCF / Revenue", "Cash Flow", "%", "higher",
             lambda v: _pct(v.get("fcf"), v.get("revenue")), good=8, weak=0),
    RatioDef("fcf_debt", "FCF / Total debt", "Cash Flow", "%", "higher",
             lambda v: _pct(v.get("fcf"), v.get("total_debt")), good=15, weak=2),
    RatioDef("capex_revenue", "Capex / Revenue", "Cash Flow", "%", "neutral",
             lambda v: _pct(v.get("capex"), v.get("revenue"))),
    RatioDef("capex_da", "Capex / D&A (reinvestment)", "Cash Flow", "x", "neutral",
             lambda v: sdiv(v.get("capex"), v.get("depreciation_amortization"))),
    RatioDef("cash_conversion", "Cash conversion (CFO / EBITDA)", "Cash Flow", "%", "higher",
             lambda v: _pct(v.get("cfo"), v.get("ebitda")), good=80, weak=40),
    RatioDef("dividend_payout", "Dividend payout (Div / Net income)", "Cash Flow", "%", "neutral",
             lambda v: _pct(v.get("dividends_paid"), v.get("net_income"))),
    RatioDef("retained_cf", "Retained cash flow (CFO - Capex - Dividends)", "Cash Flow", "amount",
             "higher", lambda v: v.get("fcf_after_div")),
]

RATIO_BY_KEY: dict[str, RatioDef] = {r.key: r for r in RATIOS}


def compute_all(v: dict) -> dict[str, Number]:
    return {r.key: r.compute(v) for r in RATIOS}


def by_category() -> dict[str, list[RatioDef]]:
    out: dict[str, list[RatioDef]] = {c: [] for c in CATEGORIES}
    for r in RATIOS:
        out[r.category].append(r)
    return out


def fmt(value: Number, unit: str, currency: str = "") -> str:
    if value is None:
        return "n/m"
    if unit == "%":
        return f"{value:,.1f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    if unit == "ratio":
        return f"{value:,.2f}"
    if unit == "amount":
        prefix = f"{currency} " if currency else ""
        return f"{prefix}{value:,.0f}"
    return f"{value:,.2f}"
