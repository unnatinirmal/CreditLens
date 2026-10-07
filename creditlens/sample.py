"""Sample data: a realistic raw multi-year statement workbook (for the upload
path) plus a blank canonical template and a canonical sample (for the manual
editor path)."""

from __future__ import annotations

import io

import pandas as pd

from .schema import LINE_ITEMS

SAMPLE_COMPANY = "Northwind Components Inc."
SAMPLE_INDUSTRY = "Industrial manufacturing (auto components)"
SAMPLE_UNIT = "millions"

_FYS = ["FY2020", "FY2021", "FY2022", "FY2023", "FY2024"]

# label -> per-year value. Rows whose value list is empty are section headers.
_RAW_ROWS: list[tuple[str, list]] = [
    ("STATEMENT OF PROFIT OR LOSS", []),
    ("Revenue from operations", [1050, 1140, 1180, 1264, 1298]),
    ("Other income", [5, 5, 4, 4, 3]),
    ("Cost of materials consumed", [609, 673, 708, 777, 818]),
    ("Changes in inventories of finished goods", [0, 0, 0, 0, 0]),
    ("Employee benefits expense", [136, 150, 159, 174, 184]),
    ("Other expenses", [84, 93, 100, 111, 119]),
    ("Depreciation and amortisation expense", [52, 58, 62, 66, 71]),
    ("Finance costs", [26, 30, 34, 41, 52]),
    ("Profit before tax", [148, 141, 121, 99, 57]),
    ("Tax expense", [37, 35, 30, 20, 12]),
    ("Profit for the year", [111, 106, 91, 79, 45]),
    ("", []),
    ("BALANCE SHEET", []),
    ("ASSETS", []),
    ("Non-current assets", []),
    ("Property, plant and equipment", [470, 500, 512, 548, 579]),
    ("Goodwill", [40, 40, 40, 40, 40]),
    ("Deferred tax assets", [18, 20, 22, 24, 26]),
    ("Total non-current assets", [528, 560, 574, 612, 645]),
    ("Current assets", []),
    ("Inventories", [180, 196, 205, 231, 258]),
    ("Trade receivables", [150, 165, 172, 188, 201]),
    ("Short-term investments", [12, 10, 10, 8, 5]),
    ("Cash and cash equivalents", [60, 66, 71, 66, 58]),
    ("Prepaid expenses and other current assets", [20, 22, 24, 26, 27]),
    ("Total current assets", [422, 459, 482, 519, 549]),
    ("Total assets", [950, 1019, 1056, 1131, 1194]),
    ("EQUITY AND LIABILITIES", []),
    ("Equity", []),
    ("Equity share capital", [100, 100, 100, 100, 100]),
    ("Retained earnings", [190, 200, 178, 183, 166]),
    ("Other reserves", [40, 38, 40, 40, 40]),
    ("Total equity", [330, 338, 318, 323, 306]),
    ("Non-current liabilities", []),
    ("Long-term borrowings", [300, 330, 360, 400, 450]),
    ("Lease liabilities (non-current)", [30, 33, 35, 38, 42]),
    ("Deferred tax liabilities", [20, 22, 24, 26, 28]),
    ("Long-term provisions", [22, 23, 25, 27, 29]),
    ("Total non-current liabilities", [372, 408, 444, 491, 549]),
    ("Current liabilities", []),
    ("Trade payables", [130, 140, 148, 152, 149]),
    ("Short-term borrowings", [40, 48, 55, 66, 82]),
    ("Current portion of long-term debt", [25, 28, 30, 34, 39]),
    ("Lease liabilities (current)", [8, 9, 10, 11, 12]),
    ("Accrued expenses and other current liabilities", [45, 48, 51, 54, 57]),
    ("Total current liabilities", [248, 273, 294, 317, 339]),
    ("Total liabilities", [620, 681, 738, 808, 888]),
    ("", []),
    ("STATEMENT OF CASH FLOWS", []),
    ("Net cash from operating activities", [150, 145, 138, 121, 104]),
    ("Purchase of property, plant and equipment", [-90, -100, -112, -118, -121]),
    ("Net cash used in investing activities", [-92, -103, -116, -121, -124]),
    ("Interest paid", [-25, -29, -33, -40, -50]),
    ("Dividends paid", [-15, -15, -12, -12, -12]),
    ("Net cash used in financing activities", [-40, -35, -30, -18, 12]),
]


def sample_workbook_xlsx() -> bytes:
    """Raw stacked statements, 5 fiscal years - what a user would upload."""
    rows = [[""] + _FYS]
    for label, vals in _RAW_ROWS:
        rows.append([label] + (list(vals) if vals else [None] * len(_FYS)))
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, header=False, sheet_name="Financials")
        ws = xw.sheets["Financials"]
        ws.column_dimensions["A"].width = 46
        for col in "BCDEFG"[: len(_FYS)]:
            ws.column_dimensions[col].width = 12
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# canonical-format helpers (manual editor path)
# --------------------------------------------------------------------------- #
def _canonical_frame(data: dict[str, dict[str, float | None]]) -> pd.DataFrame:
    periods = list(data)
    rows = [[li.label] + [data[p].get(li.key) for p in periods] for li in LINE_ITEMS]
    return pd.DataFrame(rows, columns=["Line item"] + periods)


def _to_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Financials")
        xw.sheets["Financials"].column_dimensions["A"].width = 44
    return buf.getvalue()


def blank_template_xlsx(period_names: list[str] | None = None) -> bytes:
    period_names = period_names or ["FY2023", "FY2024"]
    empty = {p: {li.key: None for li in LINE_ITEMS} for p in period_names}
    return _to_xlsx(_canonical_frame(empty))


def blank_canonical_periods(period_names: list[str]) -> dict[str, dict]:
    return {p: {li.key: None for li in LINE_ITEMS} for p in period_names}
