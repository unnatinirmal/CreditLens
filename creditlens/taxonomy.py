"""Line-item taxonomy: the buckets the classifier assigns raw statement rows to,
and how those buckets roll up into the canonical keys the ratio engine expects.
"""

from __future__ import annotations

# ---- category ids -------------------------------------------------------------
# income statement
IS_REVENUE = "is.revenue"
IS_COGS = "is.cogs"
IS_GROSS_PROFIT = "is.gross_profit"
IS_OPEX = "is.opex"                       # operating expenses EXCLUDING D&A
IS_DA = "is.depreciation_amortization"
IS_OTHER_OP_INCOME = "is.other_operating_income"
IS_EBIT = "is.ebit"
IS_EBITDA = "is.ebitda"
IS_INTEREST_EXPENSE = "is.interest_expense"
IS_INTEREST_INCOME = "is.interest_income"
IS_OTHER_NONOP = "is.other_nonoperating"
IS_PRETAX = "is.pretax_income"
IS_TAX = "is.tax_expense"
IS_NET_INCOME = "is.net_income"
# balance sheet - assets
BA_CASH = "ba.cash"
BA_STI = "ba.short_term_investments"
BA_RECEIVABLES = "ba.receivables"
BA_INVENTORY = "ba.inventory"
BA_OTHER_CA = "ba.other_current_assets"
BA_CA_TOTAL = "ba.current_assets_total"
BA_PPE = "ba.ppe"
BA_INTANGIBLES = "ba.intangibles"
BA_OTHER_NCA = "ba.other_noncurrent_assets"
BA_NCA_TOTAL = "ba.noncurrent_assets_total"
BA_TOTAL_ASSETS = "ba.total_assets"
# balance sheet - liabilities & equity
BL_PAYABLES = "bl.payables"
BL_STD = "bl.short_term_debt"             # incl. current portion of LTD + current leases
BL_OTHER_CL = "bl.other_current_liabilities"
BL_CL_TOTAL = "bl.current_liabilities_total"
BL_LTD = "bl.long_term_debt"              # incl. non-current leases
BL_OTHER_NCL = "bl.other_noncurrent_liabilities"
BL_NCL_TOTAL = "bl.noncurrent_liabilities_total"
BL_TOTAL_LIAB = "bl.total_liabilities"
EQ_TOTAL = "eq.total_equity"
EQ_COMPONENTS = "eq.components"           # share capital, reserves, retained earnings, NCI
# cash flow
CF_CFO = "cf.cfo"
CF_CAPEX = "cf.capex"
CF_DIVIDENDS = "cf.dividends_paid"
CF_INTEREST_PAID = "cf.interest_paid"
CF_CFI = "cf.cfi"
CF_CFF = "cf.cff"
# misc
IGNORE = "ignore"

CATEGORY_LABELS: dict[str, str] = {
    IS_REVENUE: "Revenue / net sales",
    IS_COGS: "Cost of sales / materials",
    IS_GROSS_PROFIT: "Gross profit (subtotal)",
    IS_OPEX: "Operating expenses (ex-D&A)",
    IS_DA: "Depreciation & amortisation",
    IS_OTHER_OP_INCOME: "Other operating income",
    IS_EBIT: "EBIT / operating profit (subtotal)",
    IS_EBITDA: "EBITDA (subtotal)",
    IS_INTEREST_EXPENSE: "Interest / finance expense",
    IS_INTEREST_INCOME: "Interest / finance income",
    IS_OTHER_NONOP: "Other non-operating items",
    IS_PRETAX: "Profit before tax (subtotal)",
    IS_TAX: "Income tax expense",
    IS_NET_INCOME: "Net income (subtotal)",
    BA_CASH: "Cash & cash equivalents",
    BA_STI: "Short-term investments",
    BA_RECEIVABLES: "Trade & other receivables",
    BA_INVENTORY: "Inventory",
    BA_OTHER_CA: "Other current assets",
    BA_CA_TOTAL: "Total current assets (reported)",
    BA_PPE: "Property, plant & equipment / fixed assets",
    BA_INTANGIBLES: "Goodwill & intangibles",
    BA_OTHER_NCA: "Other non-current assets",
    BA_NCA_TOTAL: "Total non-current assets (reported)",
    BA_TOTAL_ASSETS: "Total assets",
    BL_PAYABLES: "Trade & other payables",
    BL_STD: "Short-term debt / current portion of LTD / current leases",
    BL_OTHER_CL: "Other current liabilities",
    BL_CL_TOTAL: "Total current liabilities (reported)",
    BL_LTD: "Long-term debt / borrowings / non-current leases",
    BL_OTHER_NCL: "Other non-current liabilities",
    BL_NCL_TOTAL: "Total non-current liabilities (reported)",
    BL_TOTAL_LIAB: "Total liabilities",
    EQ_TOTAL: "Total equity",
    EQ_COMPONENTS: "Equity component (capital / reserves / retained earnings)",
    CF_CFO: "Cash flow from operations",
    CF_CAPEX: "Capital expenditure",
    CF_DIVIDENDS: "Dividends paid",
    CF_INTEREST_PAID: "Interest paid (cash)",
    CF_CFI: "Cash flow from investing",
    CF_CFF: "Cash flow from financing",
    IGNORE: "— ignore this row —",
}

# grid dropdown grouping / order
CATEGORY_GROUPS: list[tuple[str, list[str]]] = [
    ("Income statement", [IS_REVENUE, IS_COGS, IS_GROSS_PROFIT, IS_OPEX, IS_DA,
                          IS_OTHER_OP_INCOME, IS_EBIT, IS_EBITDA, IS_INTEREST_EXPENSE,
                          IS_INTEREST_INCOME, IS_OTHER_NONOP, IS_PRETAX, IS_TAX, IS_NET_INCOME]),
    ("Assets", [BA_CASH, BA_STI, BA_RECEIVABLES, BA_INVENTORY, BA_OTHER_CA, BA_CA_TOTAL,
                BA_PPE, BA_INTANGIBLES, BA_OTHER_NCA, BA_NCA_TOTAL, BA_TOTAL_ASSETS]),
    ("Liabilities & equity", [BL_PAYABLES, BL_STD, BL_OTHER_CL, BL_CL_TOTAL, BL_LTD,
                              BL_OTHER_NCL, BL_NCL_TOTAL, BL_TOTAL_LIAB, EQ_TOTAL, EQ_COMPONENTS]),
    ("Cash flow", [CF_CFO, CF_CAPEX, CF_DIVIDENDS, CF_INTEREST_PAID, CF_CFI, CF_CFF]),
    ("Other", [IGNORE]),
]
ALL_CATEGORIES = [c for _, cats in CATEGORY_GROUPS for c in cats]

# categories that must appear at most once per period - if the classifier finds
# several, it keeps the best-matching row and demotes the rest to "subtotal".
SINGLETON = {IS_REVENUE, IS_GROSS_PROFIT, IS_EBIT, IS_EBITDA, IS_PRETAX, IS_NET_INCOME,
             BA_TOTAL_ASSETS, BL_TOTAL_LIAB, EQ_TOTAL, CF_CFO, CF_CFI, CF_CFF}

# categories that are always reconciliation-only (never summed into an aggregate)
SUBTOTAL_ONLY = {BA_CA_TOTAL, BA_NCA_TOTAL, BL_CL_TOTAL, BL_NCL_TOTAL}

# cost / outflow lines that reports may show as negative - stored as magnitudes
ABS_CATEGORIES = {IS_COGS, IS_OPEX, IS_DA, IS_INTEREST_EXPENSE, CF_CAPEX,
                  CF_DIVIDENDS, CF_INTEREST_PAID}

# categories that are summed from several rows - a row here whose label starts
# with "total"/"subtotal" is almost certainly a roll-up and gets demoted.
ADDITIVE_COMPONENTS = {IS_COGS, IS_OPEX, IS_DA, IS_INTEREST_EXPENSE, IS_INTEREST_INCOME,
                       BA_CASH, BA_STI, BA_RECEIVABLES, BA_INVENTORY, BA_OTHER_CA, BA_PPE,
                       BA_INTANGIBLES, BA_OTHER_NCA, BL_PAYABLES, BL_STD, BL_OTHER_CL, BL_LTD,
                       BL_OTHER_NCL, EQ_COMPONENTS, CF_CAPEX, CF_DIVIDENDS}

# if the key category has a value for a period, suppress the value category
# (avoids double-counting reported equity against its own components)
SUPPRESSED_BY = {EQ_COMPONENTS: EQ_TOTAL}

# bucket -> canonical key consumed by model.derive() / ratios / scoring
CATEGORY_TO_CANONICAL: dict[str, str] = {
    IS_REVENUE: "revenue", IS_COGS: "cogs", IS_GROSS_PROFIT: "gross_profit",
    IS_OPEX: "sga", IS_DA: "depreciation_amortization", IS_EBIT: "ebit",
    IS_EBITDA: "ebitda", IS_INTEREST_EXPENSE: "interest_expense",
    IS_INTEREST_INCOME: "interest_income", IS_PRETAX: "pretax_income",
    IS_TAX: "tax_expense", IS_NET_INCOME: "net_income",
    BA_CASH: "cash", BA_STI: "short_term_investments", BA_RECEIVABLES: "accounts_receivable",
    BA_INVENTORY: "inventory", BA_OTHER_CA: "other_current_assets", BA_PPE: "net_ppe",
    BA_INTANGIBLES: "intangibles", BA_OTHER_NCA: "other_noncurrent_assets",
    BA_TOTAL_ASSETS: "total_assets",
    BL_PAYABLES: "accounts_payable", BL_STD: "short_term_debt",
    BL_OTHER_CL: "other_current_liabilities", BL_LTD: "long_term_debt",
    BL_OTHER_NCL: "other_liabilities", BL_TOTAL_LIAB: "total_liabilities",
    EQ_TOTAL: "total_equity", EQ_COMPONENTS: "total_equity",
    CF_CFO: "cfo", CF_CAPEX: "capex", CF_DIVIDENDS: "dividends_paid",
    CF_INTEREST_PAID: "interest_paid",
}

# section ids used while scanning a sheet
SEC_INCOME = "income"
SEC_ASSETS = "assets"
SEC_ASSETS_CURRENT = "assets_current"
SEC_ASSETS_NONCURRENT = "assets_noncurrent"
SEC_LIAB = "liabilities"
SEC_LIAB_CURRENT = "liabilities_current"
SEC_LIAB_NONCURRENT = "liabilities_noncurrent"
SEC_EQUITY = "equity"
SEC_CF = "cashflow"
SEC_CF_OP = "cashflow_operating"
SEC_CF_INV = "cashflow_investing"
SEC_CF_FIN = "cashflow_financing"
SEC_UNKNOWN = "unknown"

INCOME_SECTIONS = {SEC_INCOME, SEC_UNKNOWN}
BALANCE_SECTIONS = {SEC_ASSETS, SEC_ASSETS_CURRENT, SEC_ASSETS_NONCURRENT, SEC_LIAB,
                    SEC_LIAB_CURRENT, SEC_LIAB_NONCURRENT, SEC_EQUITY, SEC_UNKNOWN}
CF_SECTIONS = {SEC_CF, SEC_CF_OP, SEC_CF_INV, SEC_CF_FIN, SEC_UNKNOWN}


def default_role(category: str) -> str:
    if category == IGNORE:
        return "ignore"
    if category in SUBTOTAL_ONLY:
        return "subtotal"
    return "value"
