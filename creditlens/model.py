"""Turn a sparse set of entered line items into a complete, ratio-ready dict.

``derive`` never overwrites a value the user supplied - it only fills blanks and
adds a few computed aggregates (net debt, FFO, capital employed, ...).
"""

from __future__ import annotations

from typing import Optional

Number = Optional[float]


def _f(x) -> Number:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        # treat NaN as missing
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def _sum(*vals) -> Number:
    present = [v for v in (_f(x) for x in vals) if v is not None]
    return sum(present) if present else None


def derive(raw: dict) -> dict:
    """Return a new dict with blanks filled and helper aggregates added."""
    v: dict = {k: _f(x) for k, x in raw.items()}

    def g(key) -> Number:
        return v.get(key)

    def setdefault(key, value):
        if v.get(key) is None and value is not None:
            v[key] = value

    # --- income statement bridges ---------------------------------------
    if g("gross_profit") is None and g("revenue") is not None and g("cogs") is not None:
        v["gross_profit"] = g("revenue") - g("cogs")

    da = g("depreciation_amortization") or 0.0
    if g("ebitda") is None and g("ebit") is not None:
        v["ebitda"] = g("ebit") + da
    if g("ebit") is None and g("ebitda") is not None:
        v["ebit"] = g("ebitda") - da
    # nature-/function-of-expense P&L: sga is operating expense EXCLUDING D&A, so
    # (gross profit or revenue) - sga = EBITDA, and EBIT = EBITDA - D&A.
    if g("ebitda") is None and g("sga") is not None:
        opex_base = g("gross_profit") if g("gross_profit") is not None else g("revenue")
        if opex_base is not None:
            v["ebitda"] = opex_base - g("sga")
            if g("ebit") is None:
                v["ebit"] = v["ebitda"] - da

    if g("pretax_income") is None and g("ebit") is not None and g("interest_expense") is not None:
        v["pretax_income"] = g("ebit") - g("interest_expense") + (g("interest_income") or 0.0)
    if g("tax_expense") is None and g("pretax_income") is not None and g("net_income") is not None:
        v["tax_expense"] = g("pretax_income") - g("net_income")
    if g("net_income") is None and g("pretax_income") is not None:
        v["net_income"] = g("pretax_income") - (g("tax_expense") or 0.0)

    # effective tax rate (clamped to a sane band, default 25%)
    pretax, tax = g("pretax_income"), g("tax_expense")
    if pretax and pretax > 0 and tax is not None:
        rate = tax / pretax
    else:
        rate = 0.25
    v["_tax_rate"] = min(max(rate, 0.0), 0.5)

    # --- balance sheet -------------------------------------------------
    v["total_debt"] = _sum(g("short_term_debt"), g("long_term_debt")) or 0.0
    setdefault("current_assets", _sum(
        g("cash"), g("short_term_investments"), g("accounts_receivable"),
        g("inventory"), g("other_current_assets")))
    setdefault("current_liabilities", _sum(
        g("accounts_payable"), g("short_term_debt"), g("other_current_liabilities")))

    # sum liability components first, then fall back to the balance-sheet identity
    if g("total_liabilities") is None:
        v["total_liabilities"] = _sum(
            g("current_liabilities"), g("long_term_debt"), g("other_liabilities"))
    if g("total_liabilities") is None and g("total_assets") is not None and g("total_equity") is not None:
        v["total_liabilities"] = g("total_assets") - g("total_equity")
    if g("total_equity") is None and g("total_assets") is not None and g("total_liabilities") is not None:
        v["total_equity"] = g("total_assets") - g("total_liabilities")
    if g("total_assets") is None and g("total_liabilities") is not None and g("total_equity") is not None:
        v["total_assets"] = g("total_liabilities") + g("total_equity")

    cash_like = _sum(g("cash"), g("short_term_investments")) or 0.0
    v["_cash_like"] = cash_like
    v["net_debt"] = v["total_debt"] - cash_like

    # --- cash flow -------------------------------------------------
    setdefault("interest_paid", g("interest_expense"))
    cfo, capex = g("cfo"), g("capex")
    v["fcf"] = None if cfo is None or capex is None else cfo - capex
    if v["fcf"] is not None:
        v["fcf_after_div"] = v["fcf"] - (g("dividends_paid") or 0.0)
    else:
        v["fcf_after_div"] = None

    # funds from operations (approx.)
    ni = g("net_income")
    v["ffo"] = None if ni is None else ni + da

    # --- derived denominators -------------------------------------------------
    ta, cl = g("total_assets"), g("current_liabilities")
    v["capital_employed"] = None if ta is None or cl is None else ta - cl
    te = g("total_equity")
    v["invested_capital"] = None if te is None else v["total_debt"] + te
    v["working_capital"] = None if g("current_assets") is None or cl is None else g("current_assets") - cl

    return v


def missing_required(raw: dict) -> list[str]:
    from .schema import LINE_ITEMS
    return [li.label for li in LINE_ITEMS if li.required and _f(raw.get(li.key)) is None]
