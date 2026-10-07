"""Flexible extraction of raw statement rows from uploaded workbooks.

Handles: multiple files, multiple sheets per file, statements stacked in one
sheet with section-header rows, and 1..N period columns per sheet. Output is a
list of :class:`creditlens.classify.ClassifiedRow` plus the ordered list of
periods discovered across everything.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

from .classify import ClassifiedRow, detect_section, normalize, section_from_sheet_name

_NUM_RE = re.compile(r"[^0-9.\-]")
_RANGE4_2 = re.compile(r"(\d{4})\s*[-/]\s*'?(\d{2})(?!\d)")     # 2023-24
_RANGE2_2 = re.compile(r"(?<!\d)'?(\d{2})\s*[-/]\s*'?(\d{2})(?!\d)")  # 23-24 / FY23-24
_FULLYEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")      # 2023
_SHORTYEAR = re.compile(r"(?:fy|f\.y\.?|ye)\s*'?(\d{2})(?!\d)|'(\d{2})(?!\d)", re.I)


@dataclass(frozen=True)
class Period:
    label: str      # header text as shown to the user
    key: int        # sortable fiscal-year key

    def __hash__(self):
        return hash((self.label, self.key))


def parse_number(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return None if raw != raw else float(raw)
    s = str(raw).strip()
    if s.lower() in ("", "-", "--", "—", "–", "n/a", "na", "nm", "n/m", "nil"):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.endswith("-")
    s = s.replace(",", "").replace("%", "").replace("–", "-")
    s = _NUM_RE.sub("", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -abs(val) if neg else val


def _widen2(tok: str) -> int:
    n = int(tok)
    return 2000 + n if n < 80 else 1900 + n


def _year_key(text: str) -> int | None:
    s = str(text).strip()
    if not s:
        return None
    m = _RANGE4_2.search(s)            # "2023-24" -> fiscal year ending 2024
    if m:
        return _widen2(m.group(2))
    full = [int(x) for x in _FULLYEAR.findall(s)]
    if full:
        return max(full)              # "year ended 31 March 2023" -> 2023
    m = _RANGE2_2.search(s)           # "23-24"
    if m:
        return max(_widen2(m.group(1)), _widen2(m.group(2)))
    m = _SHORTYEAR.search(s)          # "FY23" / "'23"
    if m:
        return _widen2(m.group(1) or m.group(2))
    return None


def _looks_like_year(text) -> bool:
    return _year_key(text) is not None


def _find_header_row(df: pd.DataFrame) -> int:
    best_row, best_hits = 0, -1
    for i in range(min(20, len(df))):
        hits = sum(_looks_like_year(x) for x in df.iloc[i].tolist())
        if hits > best_hits:
            best_row, best_hits = i, hits
    return best_row


def _extract_sheet(df: pd.DataFrame, file: str, sheet: str,
                   start_rid: int) -> tuple[list[ClassifiedRow], list[Period], int]:
    df = df.dropna(how="all").reset_index(drop=True)
    if df.empty or df.shape[1] < 2:
        return [], [], start_rid

    hrow = _find_header_row(df)
    header = df.iloc[hrow].tolist()
    label_col = 0

    period_cols: list[tuple[int, Period]] = []
    for c in range(1, df.shape[1]):
        yk = _year_key(header[c]) if c < len(header) else None
        if yk is not None:
            period_cols.append((c, Period(str(header[c]).strip(), yk)))

    if not period_cols:  # no parseable years - treat numeric columns positionally
        pos = 1
        for c in range(1, df.shape[1]):
            col_vals = [parse_number(x) for x in df.iloc[hrow + 1:, c].tolist()]
            if sum(v is not None for v in col_vals) >= 2:
                lbl = str(header[c]).strip() if c < len(header) and str(header[c]).strip() else f"Period {pos}"
                period_cols.append((c, Period(lbl, pos)))
                pos += 1

    if not period_cols:
        return [], [], start_rid

    section = section_from_sheet_name(sheet)
    rows: list[ClassifiedRow] = []
    rid = start_rid
    for i in range(hrow + 1, len(df)):
        raw_label = df.iat[i, label_col]
        label = "" if pd.isna(raw_label) else str(raw_label).strip()
        if not label:
            continue
        norm = normalize(label)
        values = {p.key: parse_number(df.iat[i, c]) for c, p in period_cols}
        has_num = any(v is not None for v in values.values())
        if not has_num:
            section = detect_section(norm, section)  # header / section row
            continue
        rows.append(ClassifiedRow(
            rid=f"{file}|{sheet}|{rid}", file=file, sheet=sheet, label=label,
            norm=norm, section=section, values=values))
        rid += 1
    return rows, [p for _, p in period_cols], rid


def _read_workbook(data: bytes, name: str) -> dict[str, pd.DataFrame]:
    buf = io.BytesIO(data)
    if name.lower().endswith(".csv"):
        return {"csv": pd.read_csv(buf, header=None, dtype=object)}
    xls = pd.read_excel(buf, sheet_name=None, header=None, dtype=object)
    return xls


def extract_files(files: list[tuple[str, bytes]]) -> tuple[list[ClassifiedRow], list[Period], list[str]]:
    """files: list of (name, bytes), earliest-uploaded first. Later files win on
    (normalised label, period) overlaps."""
    all_rows: list[ClassifiedRow] = []
    period_by_key: dict[int, Period] = {}
    warnings: list[str] = []
    rid = 0

    for name, data in files:
        try:
            sheets = _read_workbook(data, name)
        except Exception as exc:
            warnings.append(f"{name}: could not read ({exc}).")
            continue
        file_rows = 0
        for sheet, df in sheets.items():
            try:
                rows, periods, rid = _extract_sheet(df, name, sheet, rid)
            except Exception as exc:
                warnings.append(f"{name}[{sheet}]: skipped ({exc}).")
                continue
            for p in periods:
                period_by_key.setdefault(p.key, p)
            all_rows.extend(rows)
            file_rows += len(rows)
        if file_rows == 0:
            warnings.append(f"{name}: no data rows with recognisable period columns.")

    periods = sorted(period_by_key.values(), key=lambda p: p.key)
    key_to_label = {p.key: p.label for p in periods}

    # merge duplicates across files (values are keyed by fiscal-year int here):
    # a later file overrides a value for the same (normalised label, year).
    merged: dict[str, ClassifiedRow] = {}
    for r in all_rows:
        if r.norm not in merged:
            merged[r.norm] = ClassifiedRow(
                rid=r.rid, file=r.file, sheet=r.sheet, label=r.label, norm=r.norm,
                section=r.section, values=dict(r.values))
        else:
            tgt = merged[r.norm]
            for k, v in r.values.items():
                if v is not None:
                    tgt.values[k] = v
            if r.file not in tgt.file:
                tgt.file = f"{tgt.file}, {r.file}"

    # re-key every row's values by the canonical period label and fill gaps
    plabels = [p.label for p in periods]
    for r in merged.values():
        r.values = {key_to_label.get(k, str(k)): v for k, v in r.values.items()}
        for pl in plabels:
            r.values.setdefault(pl, None)

    return list(merged.values()), periods, warnings
