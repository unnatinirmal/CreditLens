"""Rule-based classification of raw statement rows into the taxonomy, plus
aggregation of classified rows into the canonical per-period values dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import taxonomy as T


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #
def normalize(text) -> str:
    s = str(text).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# section detection (runs on header rows - rows with a label but no numbers)
# --------------------------------------------------------------------------- #
_SECTION_RULES: list[tuple[str, str]] = [
    (r"cash flow.*operating|operating activities", T.SEC_CF_OP),
    (r"cash flow.*investing|investing activities", T.SEC_CF_INV),
    (r"cash flow.*financing|financing activities", T.SEC_CF_FIN),
    (r"statement of cash flow|cash flow statement|cash flows", T.SEC_CF),
    (r"non current asset|noncurrent asset|fixed asset", T.SEC_ASSETS_NONCURRENT),
    (r"current asset", T.SEC_ASSETS_CURRENT),
    (r"non current liabilit|noncurrent liabilit", T.SEC_LIAB_NONCURRENT),
    (r"current liabilit", T.SEC_LIAB_CURRENT),
    (r"equity and liabilit|liabilities and equity", T.SEC_LIAB),
    (r"shareholders? fund|capital and reserve|total equity|equity$", T.SEC_EQUITY),
    (r"^assets$|^assets ", T.SEC_ASSETS),
    (r"^liabilit", T.SEC_LIAB),
    (r"balance sheet|statement of financial position", T.SEC_ASSETS),
    (r"income statement|profit and loss|profit or loss|statement of operations|"
     r"statement of comprehensive income|statement of profit", T.SEC_INCOME),
]


def detect_section(norm_label: str, current: str) -> str:
    for pat, sec in _SECTION_RULES:
        if re.search(pat, norm_label):
            return sec
    return current


def section_from_sheet_name(name: str) -> str:
    return detect_section(normalize(name), T.SEC_UNKNOWN)


# --------------------------------------------------------------------------- #
# line classification
# --------------------------------------------------------------------------- #
# (regex, category, {sections}). First match wins. None sections => any.
_RULES: list[tuple[str, str, set | None]] = [
    # ----- cash flow (checked before balance-sheet cash lines) -----
    (r"cash (generated (from|by)|flows? (from|used)|from|used).*operat|net cash.*operat|"
     r"operating activities", T.CF_CFO, T.CF_SECTIONS),
    (r"cash flows? (from|used).*invest|net cash.*invest|investing activities",
     T.CF_CFI, T.CF_SECTIONS),
    (r"cash flows? (from|used).*financ|net cash.*financ|financing activities",
     T.CF_CFF, T.CF_SECTIONS),
    (r"(purchase|acquisition|addition).*(propert|plant|equipment|fixed asset|ppe|"
     r"tangible|intangible|capital work)|capital expenditure|capex|payment for.*(propert|"
     r"fixed asset)", T.CF_CAPEX, T.CF_SECTIONS),
    (r"dividend.*(paid|distribut)|payment of dividend|dividend to shareholder",
     T.CF_DIVIDENDS, T.CF_SECTIONS),
    (r"interest paid|finance cost.*paid", T.CF_INTEREST_PAID, T.CF_SECTIONS),

    # ----- income statement -----
    (r"interest income|finance income|investment income", T.IS_INTEREST_INCOME, T.INCOME_SECTIONS),
    (r"other operating income|other income", T.IS_OTHER_OP_INCOME, T.INCOME_SECTIONS),
    (r"revenue from operation|net sales|sales revenue|total revenue|revenue$|turnover|"
     r"income from operation|total income", T.IS_REVENUE, T.INCOME_SECTIONS),
    (r"changes? in inventor|change in stock", T.IS_COGS, T.INCOME_SECTIONS),
    (r"cost of (goods sold|sales|revenue|material)|cogs|material.*consumed|"
     r"purchase(s)? of (stock|traded goods|finished goods)", T.IS_COGS, T.INCOME_SECTIONS),
    (r"gross profit|gross margin", T.IS_GROSS_PROFIT, T.INCOME_SECTIONS),
    (r"depreciation|amorti[sz]ation|depletion", T.IS_DA, T.INCOME_SECTIONS),
    (r"earnings before interest.*tax.*depreciation|ebitda", T.IS_EBITDA, T.INCOME_SECTIONS),
    (r"operating profit|operating income|profit from operation|result.*from operating|"
     r"ebit\b|earnings before interest and tax", T.IS_EBIT, T.INCOME_SECTIONS),
    (r"finance cost|interest expense|interest on (borrowing|debt|loan)|interest and finance|"
     r"borrowing cost", T.IS_INTEREST_EXPENSE, T.INCOME_SECTIONS),
    (r"(selling|distribution|administrat|admin|employee benefit|staff|personnel|wages|"
     r"salaries|other|operating|overhead|marketing|rent|power and fuel|freight).*"
     r"(expense|cost)|expenses$|sg ?a|opex", T.IS_OPEX, T.INCOME_SECTIONS),
    (r"profit before (tax|income tax)|income before (tax|income tax)|pre tax|pbt|"
     r"earnings before tax", T.IS_PRETAX, T.INCOME_SECTIONS),
    (r"exceptional item|share of (profit|loss).*associat|fair value|foreign exchange|"
     r"other non operating|non operating", T.IS_OTHER_NONOP, T.INCOME_SECTIONS),
    (r"tax expense|income tax|provision for (income )?tax|current tax|deferred tax|taxation",
     T.IS_TAX, T.INCOME_SECTIONS),
    (r"profit for the (year|period)|profit after tax|net profit|net income|net earnings|"
     r"pat\b|profit attributable|profit or loss for", T.IS_NET_INCOME, T.INCOME_SECTIONS),

    # ----- balance sheet: assets -----
    (r"cash and cash equivalent|cash and bank|cash at bank|bank balance|cash on hand|"
     r"cash equivalent|cash$", T.BA_CASH, T.BALANCE_SECTIONS),
    (r"short term investment|current investment|marketable securit|liquid investment|"
     r"investment.*current", T.BA_STI, T.BALANCE_SECTIONS),
    (r"trade receivable|account(s)? receivable|trade and other receivable|debtor|"
     r"bills receivable|sundry debtor|receivable", T.BA_RECEIVABLES, T.BALANCE_SECTIONS),
    (r"inventor|stock in trade|stores and spares|raw material|finished good|"
     r"work in progress|goods in transit", T.BA_INVENTORY, T.BALANCE_SECTIONS),
    (r"total current asset", T.BA_CA_TOTAL, T.BALANCE_SECTIONS),
    (r"total non ?current asset|total fixed asset", T.BA_NCA_TOTAL, T.BALANCE_SECTIONS),
    (r"propert.*plant.*equipment|plant.*equipment|fixed asset|tangible asset|"
     r"plant and machinery|land and building|right of use asset|\bppe\b|"
     r"capital work in progress", T.BA_PPE, T.BALANCE_SECTIONS),
    (r"goodwill|intangible", T.BA_INTANGIBLES, T.BALANCE_SECTIONS),
    (r"deferred tax asset|investment in (associat|subsidiar|joint)|non current investment|"
     r"long term (loan|receivable|advance)|other non current asset|other financial asset",
     T.BA_OTHER_NCA, T.BALANCE_SECTIONS),
    (r"prepaid|prepayment|other current asset|current tax asset|loans and advance|"
     r"other bank balance|contract asset|advance", T.BA_OTHER_CA, T.BALANCE_SECTIONS),
    (r"total asset|^assets$", T.BA_TOTAL_ASSETS, T.BALANCE_SECTIONS),

    # ----- balance sheet: liabilities -----
    (r"current (portion|maturit).*(long term|debt|borrowing|loan)|current portion",
     T.BL_STD, T.BALANCE_SECTIONS),
    (r"short term borrowing|short term debt|short term loan|bank overdraft|"
     r"working capital (loan|facilit)|cash credit|commercial paper", T.BL_STD, T.BALANCE_SECTIONS),
    (r"trade payable|account(s)? payable|trade and other payable|creditor|bills payable|"
     r"sundry creditor", T.BL_PAYABLES, T.BALANCE_SECTIONS),
    (r"total current liabilit", T.BL_CL_TOTAL, T.BALANCE_SECTIONS),
    (r"total non ?current liabilit", T.BL_NCL_TOTAL, T.BALANCE_SECTIONS),
    (r"long term borrowing|long term debt|long term loan|term loan|bond(s)? payable|"
     r"debenture|senior note|notes payable|non current borrowing", T.BL_LTD, T.BALANCE_SECTIONS),
    (r"deferred tax liabilit|retirement benefit|pension|gratuit|other non current liabilit|"
     r"non current provision|long term provision", T.BL_OTHER_NCL, T.BALANCE_SECTIONS),
    (r"accrued|current tax liabilit|short term provision|current provision|unearned|"
     r"deferred revenue|contract liabilit|other current liabilit|advance from customer|"
     r"other payable|statutory due", T.BL_OTHER_CL, T.BALANCE_SECTIONS),
    (r"total liabilit", T.BL_TOTAL_LIAB, T.BALANCE_SECTIONS),

    # ----- equity -----
    (r"total equity|shareholder.*fund|stockholder.*equity|net worth|equity attributable|"
     r"total.*net worth", T.EQ_TOTAL, T.BALANCE_SECTIONS),
    (r"share capital|equity share|common stock|paid up capital|reserve|retained earning|"
     r"other equity|securities premium|surplus|non controlling interest|minority interest|"
     r"accumulated (profit|loss|deficit)|capital account", T.EQ_COMPONENTS, T.BALANCE_SECTIONS),
]

_COMPILED = [(re.compile(p), c, s) for p, c, s in _RULES]

# section tie-breakers for bare / ambiguous terms
_AMBIG = [
    (r"lease liabilit", {T.SEC_LIAB_CURRENT: T.BL_STD}, T.BL_LTD),
    (r"borrowing|loan|debt", {T.SEC_LIAB_CURRENT: T.BL_STD,
                              T.SEC_LIAB_NONCURRENT: T.BL_LTD}, T.BL_LTD),
    (r"provision", {T.SEC_LIAB_CURRENT: T.BL_OTHER_CL,
                    T.SEC_LIAB_NONCURRENT: T.BL_OTHER_NCL}, T.BL_OTHER_CL),
    (r"investment", {T.SEC_ASSETS_CURRENT: T.BA_STI,
                     T.SEC_ASSETS_NONCURRENT: T.BA_OTHER_NCA}, T.BA_OTHER_NCA),
]


def classify_label(norm_label: str, section: str = T.SEC_UNKNOWN) -> tuple[str, str]:
    """Return (category, role) for one normalised label."""
    if not norm_label or norm_label in ("nan", "none"):
        return T.IGNORE, "ignore"

    # specific keyword rules win; the bare-term / section tie-breakers are a
    # fallback for labels no specific rule matched (e.g. a lone "Borrowings").
    for rx, cat, secs in _COMPILED:
        if secs is not None and section not in secs:
            continue
        if rx.search(norm_label):
            role = T.default_role(cat)
            if (role == "value" and cat in T.ADDITIVE_COMPONENTS
                    and norm_label.startswith(("total ", "subtotal ", "sub total "))):
                role = "subtotal"
            return cat, role

    for pat, mapping, fallback in _AMBIG:
        if re.search(pat, norm_label):
            cat = mapping.get(section, fallback)
            return cat, T.default_role(cat)

    return T.IGNORE, "ignore"


# --------------------------------------------------------------------------- #
# post-processing: one row per singleton category per period
# --------------------------------------------------------------------------- #
_SINGLETON_PREFERENCE: dict[str, str] = {
    T.IS_REVENUE: r"revenue from operation|net sales|turnover",
    T.IS_NET_INCOME: r"profit for the (year|period)|profit after tax|net profit",
    T.IS_PRETAX: r"profit before tax|pbt",
    T.IS_EBIT: r"operating profit|profit from operation",
    T.CF_CFO: r"net cash (from|used).*operat",
}


def resolve_singletons(rows: list["ClassifiedRow"], periods: list[str]) -> None:
    """Mutates rows: for each singleton category with >1 'value' row carrying a
    figure for a period, keep the best label and demote the others to subtotal.
    """
    for cat in T.SINGLETON:
        contenders = [r for r in rows if r.category == cat and r.role == "value"]
        if len(contenders) < 2:
            continue
        pref = _SINGLETON_PREFERENCE.get(cat)
        best = None
        if pref:
            for r in contenders:
                if re.search(pref, r.norm):
                    best = r
                    break
        if best is None:  # fall back to the row with the most populated periods
            best = max(contenders, key=lambda r: sum(1 for p in periods if r.values.get(p) is not None))
        for r in contenders:
            if r is not best:
                r.role = "subtotal"
                r.note = "demoted (duplicate of a singleton line)"


# --------------------------------------------------------------------------- #
# data holders
# --------------------------------------------------------------------------- #
@dataclass
class ClassifiedRow:
    rid: str
    file: str
    sheet: str
    label: str
    norm: str
    section: str
    values: dict[str, float]
    category: str = T.IGNORE
    role: str = "value"           # value | subtotal | ignore
    source: str = "rule"          # rule | llm | user
    note: str = ""


def classify_rows(rows: list[ClassifiedRow], periods: list[str],
                  overrides: dict[str, tuple[str, str]] | None = None) -> list[ClassifiedRow]:
    """Apply rule classification (respecting user/LLM overrides keyed by norm label)."""
    overrides = overrides or {}
    for r in rows:
        if r.norm in overrides:
            r.category, r.role = overrides[r.norm]
            r.source = "user"
        elif r.source in ("rule", ""):
            r.category, r.role = classify_label(r.norm, r.section)
            r.source = "rule"
    resolve_singletons(rows, periods)
    return rows


# --------------------------------------------------------------------------- #
# aggregation -> canonical values per period
# --------------------------------------------------------------------------- #
@dataclass
class Aggregation:
    values: dict[str, dict[str, float]] = field(default_factory=dict)          # period -> canonical
    reported_subtotals: dict[str, dict[str, float]] = field(default_factory=dict)
    contributors: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # period -> canon -> labels


def aggregate(rows: list[ClassifiedRow], periods: list[str]) -> Aggregation:
    agg = Aggregation()
    for p in periods:
        agg.values[p] = {}
        agg.reported_subtotals[p] = {}
        agg.contributors[p] = {}

    # pass 1: which "value" categories carry a figure in each period
    present: dict[str, set[str]] = {p: set() for p in periods}
    for r in rows:
        if r.role != "value" or r.category == T.IGNORE:
            continue
        for p in periods:
            if r.values.get(p) is not None:
                present[p].add(r.category)

    # pass 2: sum
    for r in rows:
        if r.role == "ignore" or r.category == T.IGNORE:
            continue
        for p in periods:
            val = r.values.get(p)
            if val is None:
                continue
            if r.role == "subtotal":
                agg.reported_subtotals[p][r.category] = \
                    agg.reported_subtotals[p].get(r.category, 0.0) + val
                continue
            suppressor = T.SUPPRESSED_BY.get(r.category)
            if suppressor and suppressor in present[p]:
                continue
            canon = T.CATEGORY_TO_CANONICAL.get(r.category)
            if not canon:
                continue
            if r.category in T.ABS_CATEGORIES:
                val = abs(val)
            agg.values[p][canon] = agg.values[p].get(canon, 0.0) + val
            agg.contributors[p].setdefault(canon, []).append(r.label)
    return agg


# --------------------------------------------------------------------------- #
# reconciliation - reported subtotals vs classified sums
# --------------------------------------------------------------------------- #
_RECON_CHECKS = [
    ("Current assets", T.BA_CA_TOTAL,
     ["cash", "short_term_investments", "accounts_receivable", "inventory", "other_current_assets"]),
    ("Current liabilities", T.BL_CL_TOTAL,
     ["accounts_payable", "short_term_debt", "other_current_liabilities"]),
]


def reconciliation(agg: Aggregation, derived_by_period: dict[str, dict],
                   periods: list[str]) -> list[dict]:
    out = []
    for p in periods:
        v = derived_by_period.get(p, {})
        for name, sub_cat, comps in _RECON_CHECKS:
            reported = agg.reported_subtotals.get(p, {}).get(sub_cat)
            classified = sum(v.get(k) or 0.0 for k in comps) or None
            diff = None if reported is None or classified is None else classified - reported
            out.append({"Period": p, "Check": name, "Reported": reported,
                        "Classified": classified, "Difference": diff})
        # balance-sheet identity
        ta = v.get("total_assets")
        tl_te = None
        if v.get("total_liabilities") is not None and v.get("total_equity") is not None:
            tl_te = v["total_liabilities"] + v["total_equity"]
        out.append({"Period": p, "Check": "Assets = Liab + Equity", "Reported": ta,
                    "Classified": tl_te,
                    "Difference": None if ta is None or tl_te is None else tl_te - ta})
    return out
