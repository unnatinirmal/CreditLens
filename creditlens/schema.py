"""Canonical financial-statement line items and label aliases.

The parser maps arbitrary spreadsheet row labels onto the ``key`` values below.
Only a handful of items are strictly ``required``; everything else is either
entered directly or derived by :func:`creditlens.model.derive`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    key: str
    label: str
    statement: str  # "income" | "balance" | "cashflow"
    required: bool = False
    hint: str = ""


LINE_ITEMS: list[LineItem] = [
    # ---- Income statement -------------------------------------------------
    LineItem("revenue", "Revenue / Net sales", "income", required=True),
    LineItem("cogs", "Cost of goods sold", "income",
             hint="Used to derive gross profit if that is left blank."),
    LineItem("gross_profit", "Gross profit", "income",
             hint="Optional - derived as Revenue - COGS."),
    LineItem("sga", "Operating expenses (SG&A, ex-D&A)", "income"),
    LineItem("depreciation_amortization", "Depreciation & amortisation", "income",
             hint="Needed to bridge between EBIT and EBITDA."),
    LineItem("ebitda", "EBITDA", "income",
             hint="Optional - derived as EBIT + D&A."),
    LineItem("ebit", "EBIT / Operating income", "income",
             hint="Optional - derived as EBITDA - D&A."),
    LineItem("interest_expense", "Interest expense", "income", required=True),
    LineItem("interest_income", "Interest income", "income"),
    LineItem("pretax_income", "Pre-tax income", "income"),
    LineItem("tax_expense", "Income tax expense", "income"),
    LineItem("net_income", "Net income", "income", required=True),
    # ---- Balance sheet -------------------------------------------------
    LineItem("cash", "Cash & cash equivalents", "balance", required=True),
    LineItem("short_term_investments", "Short-term investments", "balance"),
    LineItem("accounts_receivable", "Accounts receivable", "balance"),
    LineItem("inventory", "Inventory", "balance"),
    LineItem("other_current_assets", "Other current assets", "balance"),
    LineItem("current_assets", "Total current assets", "balance",
             hint="Optional - derived by summing current-asset lines."),
    LineItem("net_ppe", "Net property, plant & equipment", "balance"),
    LineItem("total_assets", "Total assets", "balance", required=True),
    LineItem("accounts_payable", "Accounts payable", "balance"),
    LineItem("short_term_debt", "Short-term debt & current portion of LTD", "balance",
             required=True),
    LineItem("other_current_liabilities", "Other current liabilities", "balance"),
    LineItem("current_liabilities", "Total current liabilities", "balance",
             hint="Optional - derived by summing current-liability lines."),
    LineItem("long_term_debt", "Long-term debt", "balance", required=True),
    LineItem("other_liabilities", "Other non-current liabilities", "balance"),
    LineItem("total_liabilities", "Total liabilities", "balance",
             hint="Optional - derived as Total assets - Total equity."),
    LineItem("total_equity", "Total shareholders' equity", "balance", required=True),
    # ---- Cash-flow statement -------------------------------------------------
    LineItem("cfo", "Cash flow from operations", "cashflow", required=True),
    LineItem("capex", "Capital expenditure", "cashflow", required=True,
             hint="Enter as a positive number."),
    LineItem("dividends_paid", "Dividends paid", "cashflow",
             hint="Enter as a positive number."),
    LineItem("interest_paid", "Cash interest paid", "cashflow",
             hint="Optional - falls back to interest expense."),
]

KEY_TO_ITEM: dict[str, LineItem] = {li.key: li for li in LINE_ITEMS}
LABEL_TO_KEY: dict[str, str] = {li.label: li.key for li in LINE_ITEMS}
REQUIRED_KEYS: list[str] = [li.key for li in LINE_ITEMS if li.required]


def _norm(text: str) -> str:
    keep = []
    for ch in str(text).lower().strip():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -/&":
            keep.append(" ")
    return " ".join("".join(keep).split())


# Extra synonyms beyond the canonical label. Keys are normalised strings.
_SYNONYMS: dict[str, list[str]] = {
    "revenue": ["revenue", "net sales", "sales", "total revenue", "turnover",
                "net revenue", "total sales"],
    "cogs": ["cogs", "cost of goods sold", "cost of sales", "cost of revenue"],
    "gross_profit": ["gross profit", "gross income", "gross margin dollars"],
    "sga": ["sga", "sg a", "selling general and administrative", "operating expenses",
            "opex", "operating costs", "sganda"],
    "depreciation_amortization": ["depreciation amortisation", "depreciation amortization",
                                  "d a", "depreciation and amortization", "dna",
                                  "depreciation", "amortisation", "amortization"],
    "ebitda": ["ebitda", "operating profit before d a"],
    "ebit": ["ebit", "operating income", "operating profit", "income from operations"],
    "interest_expense": ["interest expense", "finance costs", "finance expense",
                         "interest and finance charges", "net finance costs"],
    "interest_income": ["interest income", "finance income", "investment income"],
    "pretax_income": ["pretax income", "pre tax income", "profit before tax", "pbt",
                      "income before taxes", "earnings before tax"],
    "tax_expense": ["tax expense", "income tax expense", "income tax", "provision for income taxes",
                    "tax"],
    "net_income": ["net income", "net profit", "profit for the year", "net earnings",
                   "profit after tax", "pat", "net income attributable to shareholders"],
    "cash": ["cash", "cash and cash equivalents", "cash equivalents", "cash and bank balances"],
    "short_term_investments": ["short term investments", "marketable securities",
                               "current investments"],
    "accounts_receivable": ["accounts receivable", "trade receivables", "receivables",
                            "debtors", "trade and other receivables"],
    "inventory": ["inventory", "inventories", "stock", "stock in trade"],
    "other_current_assets": ["other current assets", "prepaid expenses",
                             "prepayments and other current assets"],
    "current_assets": ["current assets", "total current assets"],
    "net_ppe": ["net ppe", "net property plant and equipment", "property plant and equipment",
                "ppe", "fixed assets", "net fixed assets", "tangible fixed assets"],
    "total_assets": ["total assets", "assets"],
    "accounts_payable": ["accounts payable", "trade payables", "payables", "creditors",
                         "trade and other payables"],
    "short_term_debt": ["short term debt", "current portion of long term debt",
                        "current debt", "short term borrowings", "current borrowings",
                        "current portion of ltd", "current maturities of long term debt"],
    "other_current_liabilities": ["other current liabilities", "accrued liabilities",
                                  "accrued expenses"],
    "current_liabilities": ["current liabilities", "total current liabilities"],
    "long_term_debt": ["long term debt", "long term borrowings", "non current borrowings",
                       "bonds payable", "term loans", "non current debt"],
    "other_liabilities": ["other liabilities", "other non current liabilities",
                          "deferred tax liabilities", "provisions"],
    "total_liabilities": ["total liabilities", "liabilities"],
    "total_equity": ["total equity", "shareholders equity", "stockholders equity",
                     "total shareholders equity", "net worth", "equity",
                     "total stockholders equity"],
    "cfo": ["cfo", "cash flow from operations", "operating cash flow",
            "net cash from operating activities", "cash from operations",
            "cash generated from operations"],
    "capex": ["capex", "capital expenditure", "capital expenditures",
              "purchase of property plant and equipment", "additions to ppe",
              "purchases of fixed assets", "investment in fixed assets"],
    "dividends_paid": ["dividends paid", "dividend paid", "dividends", "dividend distribution"],
    "interest_paid": ["interest paid", "cash interest paid", "interest paid in cash"],
}

ALIAS_TO_KEY: dict[str, str] = {}
for _li in LINE_ITEMS:
    ALIAS_TO_KEY[_norm(_li.label)] = _li.key
    ALIAS_TO_KEY[_norm(_li.key)] = _li.key
for _key, _names in _SYNONYMS.items():
    for _n in _names:
        ALIAS_TO_KEY[_norm(_n)] = _key


def resolve_key(label: str) -> str | None:
    """Map a spreadsheet row label onto a canonical key, or ``None``."""
    n = _norm(label)
    if not n:
        return None
    if n in ALIAS_TO_KEY:
        return ALIAS_TO_KEY[n]
    # loose contains-match as a fallback
    for alias, key in ALIAS_TO_KEY.items():
        if alias and (alias in n or n in alias):
            return key
    return None
