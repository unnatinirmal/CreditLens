"""Annual-report PDF ingest.

An annual report is a few hundred pages; the primary statements are a handful
of them, printed twice (standalone and consolidated). This module

1. locates the balance sheet, statement of profit and loss and cash-flow
   statement pages for the requested basis,
2. turns them into label + period-column tables - with Claude reading the page
   images when an API key is available, otherwise by parsing the text layer,
3. returns those tables as DataFrames shaped like an uploaded workbook sheet,
   so they flow through :func:`creditlens.parser.extract_frames` unchanged.

PDFs can be uploaded or fetched from a direct link.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field

import pandas as pd

from .memo import MemoConfigError, api_key_present, model_name

CONSOLIDATED = "consolidated"
STANDALONE = "standalone"

BS, PL, CF = "Balance Sheet", "Profit and Loss", "Cash Flows"   # names double as section hints
_STATEMENTS = (BS, PL, CF)

MAX_PDF_BYTES = 100 * 1024 * 1024
_WINDOW_AFTER_BS = 14          # PL / CF are searched this many pages after the balance sheet
_MIN_NUMBERS = 15              # a statement page is dense with figures

_NUM_TOKEN = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$|^[-–—]$")
_NOTE_TOKEN = re.compile(r"^\d{1,2}(?:\.\d{1,2})?(?:\([a-z]+\))?(?:,\d{1,2}(?:\.\d{1,2})?)*,?$", re.I)
_MARKERS = "#*^†‡@"            # footnote markers glued to figures, e.g. "1,214#"
_FIGURE = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+\.\d{1,2}")
_PERIOD = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*[-–/]\s*(\d{2}))?(?!\d)")
_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_UNIT = re.compile(r"\(\s*(?:₹|`|c|rs\.?|inr|usd|\$|us\$)?\s*in\s+(crores?|lakhs?|millions?|"
                   r"billions?|thousands?|mn|bn)\b", re.I)


class PdfIngestError(RuntimeError):
    """A PDF could not be fetched, read, or no statements were found in it."""


@dataclass
class PdfResult:
    name: str
    sheets: dict[str, pd.DataFrame]                     # sheet name -> label + period columns
    pages: dict[str, list[int]] = field(default_factory=dict)   # statement -> 1-based pages
    unit: str | None = None
    currency: str | None = None
    company: str | None = None
    method: str = "text"                                # "claude" | "text"
    warnings: list[str] = field(default_factory=list)

    @property
    def latest_year(self) -> int:
        years = [int(c[2:]) for df in self.sheets.values() for c in df.iloc[0, 1:]
                 if str(c).startswith("FY") and str(c)[2:].isdigit()]
        return max(years, default=0)


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch_pdf(url: str, timeout: float = 120.0) -> tuple[str, bytes]:
    """Download a PDF from a direct link. Returns (file name, bytes)."""
    import requests

    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise PdfIngestError(f"{url}: not an http(s) link.")
    try:
        resp = requests.get(url, headers={"User-Agent": _UA, "Accept": "application/pdf,*/*"},
                            timeout=timeout, stream=True, allow_redirects=True)
    except requests.RequestException as exc:
        raise PdfIngestError(f"{url}: download failed ({exc}).") from exc
    if resp.status_code != 200:
        hint = (" The site blocks automated downloads - download the PDF in your browser and "
                "upload it instead." if resp.status_code in (401, 403) else "")
        raise PdfIngestError(f"{url}: server returned HTTP {resp.status_code}.{hint}")

    buf = io.BytesIO()
    for chunk in resp.iter_content(1 << 16):
        buf.write(chunk)
        if buf.tell() > MAX_PDF_BYTES:
            raise PdfIngestError(f"{url}: larger than {MAX_PDF_BYTES // 2**20} MB.")
    data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise PdfIngestError(f"{url}: the link returned a web page, not a PDF. "
                             "Open it in a browser and copy the direct link to the .pdf file.")
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "report.pdf"
    return name, data


# --------------------------------------------------------------------------- #
# locating the statements
# --------------------------------------------------------------------------- #
def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _has_line(lines: list[str], pattern: str) -> bool:
    rx = re.compile(pattern, re.I)
    return any(rx.match(ln) for ln in lines)


def _page_kind(text: str) -> str | None:
    """Which primary statement a page belongs to, judged from its line items."""
    lines = _lines(text)
    head = " ".join(lines[:8]).lower()
    if (len(_FIGURE.findall(text)) < _MIN_NUMBERS or "notes to" in head or "auditor" in head
            or "changes in equity" in head):
        return None
    bs_total = _has_line(lines, r"total\s+assets\b")
    bs_rest = _has_line(lines, r"(total\s+)?(non[-\s]?)?current\s+(assets|liabilities)|"
                               r"total\s+equity|equity\s+and\s+liabilities|total\s+liabilities")
    if bs_total and bs_rest:
        return BS
    if _has_line(lines, r"total\s+equity\s+and\s+liabilities|total\s+(non[-\s]?)?current\s+liabilities"):
        return BS + "+"                                          # liabilities half on its own page
    if (_has_line(lines, r"(revenue\s+from\s+operations|total\s+income|revenue)\b")
            and _has_line(lines, r"(profit|loss).{0,20}before\s+tax|total\s+expenses|profit\s+for\s+the\s+(year|period)")):
        return PL
    if _has_line(lines, r".*(operating|investing|financing)\s+activities"):
        return CF
    return None


def _basis(text: str) -> str:
    head = " ".join(_lines(text)[:8]).lower()
    return CONSOLIDATED if "consolidated" in head else STANDALONE


def locate_statements(texts: list[str], basis: str) -> dict[str, list[int]]:
    """0-based page indices per statement for the requested basis."""
    kinds = [_page_kind(t) for t in texts]
    anchor = next((i for i, k in enumerate(kinds) if k == BS and _basis(texts[i]) == basis), None)
    if anchor is None:
        return {}
    found: dict[str, list[int]] = {BS: [anchor]}
    i = anchor + 1
    while i < len(kinds) and kinds[i] == BS + "+":
        found[BS].append(i)
        i += 1
    for stmt in (PL, CF):
        for j in range(i, min(len(kinds), anchor + _WINDOW_AFTER_BS)):
            if kinds[j] == stmt:
                run = [j]
                while run[-1] + 1 < len(kinds) and kinds[run[-1] + 1] == stmt:
                    run.append(run[-1] + 1)
                found[stmt] = run
                i = run[-1] + 1
                break
    return found


def parse_page_ranges(spec: str, n_pages: int) -> list[int]:
    """'158-165, 170' -> 0-based indices."""
    out: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise PdfIngestError(f"Bad page range '{part}' - use e.g. 158-165, 170.")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.extend(p - 1 for p in range(a, b + 1) if 1 <= p <= n_pages)
    return sorted(set(out))


# --------------------------------------------------------------------------- #
# periods / units
# --------------------------------------------------------------------------- #
def _periods(text: str) -> list[int]:
    """Fiscal years in the column header, in printed order (usually newest first)."""
    years: list[int] = []
    for ln in _lines(text)[:15]:
        for m in _PERIOD.finditer(ln):
            y = int(m.group(1))
            if m.group(2):                                       # "2023-24" -> FY2024
                y = (y // 100) * 100 + int(m.group(2))
                y += 100 if y < int(m.group(1)) else 0
            if y not in years:
                years.append(y)
    return years


def _unit(text: str) -> str | None:
    m = _UNIT.search(text[:600])
    if not m:
        return None
    u = m.group(1).lower().rstrip("s")
    return {"mn": "million", "bn": "billion"}.get(u, u) + "s"


def _currency(text: str) -> str | None:
    head = text[:600]
    if re.search(r"₹|\brs\.?\s|\binr\b|\(\s*c\s+in\s+(crore|lakh)|\(\s*`\s*in", head, re.I):
        return "INR"
    if re.search(r"\(\s*c\s+in\s+(million|billion)", head, re.I) or re.search(r"crore|lakh", head, re.I):
        return "INR"
    if re.search(r"us\$|usd|\$", head, re.I):
        return "USD"
    return None


# --------------------------------------------------------------------------- #
# text-layer table parsing (no API key needed)
# --------------------------------------------------------------------------- #
def _is_header_line(line: str) -> bool:
    return bool(_MONTH.search(line) and _PERIOD.search(line)) or bool(
        re.fullmatch(r"(notes?\s*)?((as\s+at|year\s+ended|for\s+the\s+year\s+ended)\s*)*"
                     r"((19|20)\d{2}\s*[-–]\s*\d{2}\s*)+", line, re.I))


def parse_statement_text(texts: list[str], n_periods: int) -> list[tuple[str, list[str]]]:
    """Rows of (label, value tokens) from statement pages. Rows without a full set of
    figures are kept as headings (empty value list) so section context survives."""
    rows: list[tuple[str, list[str]]] = []
    pending = ""
    for text in texts:
        for line in _lines(text):
            if _is_header_line(line) or _UNIT.search(line):
                continue
            tokens = [t.rstrip(_MARKERS) or t for t in line.split()]
            vals: list[str] = []
            while tokens and len(vals) < n_periods and _NUM_TOKEN.match(tokens[-1]):
                vals.insert(0, tokens.pop())
            while tokens and _NOTE_TOKEN.match(tokens[-1]):          # note references
                tokens.pop()
            label = " ".join(tokens).strip(" :")
            if not re.search(r"[A-Za-z]{2}", label):
                continue                                             # page numbers, stray figures
            if len(vals) == n_periods:
                if pending and label[:1].islower():                  # wrapped label
                    label = f"{pending} {label}"
                pending = ""
                rows.append((label, vals))
            else:
                if pending and label[:1].islower():
                    label = f"{pending} {label}"
                pending = label
                rows.append((label, []))
    return rows


def _frame(rows: list[tuple[str, list]], periods: list[int]) -> pd.DataFrame:
    """A raw sheet as :func:`creditlens.parser.extract_frames` expects it: header
    row first, label column then one column per period."""
    header = ["Particulars"] + [f"FY{y}" for y in periods]
    data = [header] + [[label] + (list(vals) if vals else [None] * len(periods)) for label, vals in rows]
    return pd.DataFrame(data, dtype=object)


# --------------------------------------------------------------------------- #
# Claude extraction
# --------------------------------------------------------------------------- #
_SYSTEM = ("You transcribe the primary financial statements from annual-report pages into "
           "structured data for a credit-analysis tool. You copy figures exactly as printed "
           "and never compute, estimate or fill in numbers.")

_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "currency": {"type": "string", "description": "ISO code, e.g. INR, USD"},
        "unit": {"type": "string", "description": "e.g. crores, lakhs, millions, thousands, units"},
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "enum": ["balance_sheet", "profit_and_loss", "cash_flow"]},
                    "basis": {"type": "string", "enum": [CONSOLIDATED, STANDALONE]},
                    "fiscal_years": {"type": "array", "items": {"type": "integer"},
                                     "description": "Fiscal year END of each figure column, left to right"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "values": {"type": "array", "items": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
                            },
                            "required": ["label", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["statement", "basis", "fiscal_years", "rows"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["company_name", "currency", "unit", "statements"],
    "additionalProperties": False,
}

_STMT_NAME = {"balance_sheet": BS, "profit_and_loss": PL, "cash_flow": CF}


def _subset_pdf(reader, pages: list[int]) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for p in pages:
        w.add_page(reader.pages[p])
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _claude_extract(pdf_bytes: bytes, basis: str) -> dict:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise MemoConfigError("The `anthropic` package is not installed.") from exc

    prompt = f"""These pages are from a company's annual report. Transcribe its {basis.upper()} primary
financial statements: the balance sheet, the statement of profit and loss, and the cash-flow statement.

- Skip any page that belongs to the other basis ({CONSOLIDATED if basis == STANDALONE else STANDALONE}), notes,
  or the statement of changes in equity.
- One row per printed line, top to bottom, label copied verbatim. Section headings ("Non-current assets",
  "Cash flow from operating activities", ...) are rows with an empty value per column (null).
- Drop the "Notes" reference column. Keep every figure column, left to right, in "values".
- Figures in parentheses or with a leading minus are negative. "-" / blank means null.
- Indian digit grouping (e.g. 2,58,911) is still one number: 258911.
- fiscal_years: the year each column ENDS (e.g. "31 March 2024" or "2023-24" -> 2024)."""
    client = anthropic.Anthropic()
    try:
        with client.messages.stream(
            model=model_name(),
            max_tokens=64000,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(pdf_bytes).decode()}},
                {"type": "text", "text": prompt},
            ]}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIError as exc:
        raise MemoConfigError(f"Claude PDF extraction failed: {exc}") from exc
    if msg.stop_reason not in ("end_turn", "stop_sequence"):
        raise MemoConfigError(f"Claude PDF extraction stopped early ({msg.stop_reason}).")
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def page_texts(name: str, data: bytes) -> list[str]:
    """Text layer of every page - the slow step (~20 s for a 450-page report)."""
    from pypdf import PdfReader

    try:
        return [(pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages]
    except Exception as exc:
        raise PdfIngestError(f"{name}: could not read PDF ({exc}).") from exc


def read_annual_report(name: str, data: bytes, basis: str, use_claude: bool = True,
                       page_spec: str = "", texts: list[str] | None = None) -> PdfResult:
    """Extract the primary statements for ``basis`` from one annual-report PDF.
    Pass ``texts`` (from :func:`page_texts`) to skip re-reading the text layer."""
    from pypdf import PdfReader

    if texts is None:
        texts = page_texts(name, data)
    reader = PdfReader(io.BytesIO(data))
    if sum(len(t) for t in texts) < 200:
        raise PdfIngestError(f"{name}: no text layer (scanned PDF?). Use a text-based PDF, or "
                             "enter the figures manually.")

    if page_spec.strip():
        chosen = parse_page_ranges(page_spec, len(texts))
        located: dict[str, list[int]] = {}
        for p in chosen:
            k = (_page_kind(texts[p]) or "").rstrip("+") or "Statements"
            located.setdefault(k, []).append(p)
    else:
        located = locate_statements(texts, basis)
    if not located:
        other = CONSOLIDATED if basis == STANDALONE else STANDALONE
        hint = (f" The report does contain {other} statements."
                if locate_statements(texts, other) else "")
        raise PdfIngestError(f"{name}: could not find the {basis} statements.{hint} "
                             "Enter their page numbers under 'PDF page ranges'.")

    all_pages = sorted({p for ps in located.values() for p in ps})
    first = texts[all_pages[0]]
    res = PdfResult(name=name, sheets={},
                    pages={k: [p + 1 for p in v] for k, v in located.items()},
                    unit=_unit(first), currency=_currency(first))
    missing = [s for s in _STATEMENTS if s not in located]
    if missing:
        res.warnings.append(f"{name}: {', '.join(missing)} not found for the {basis} basis.")

    if use_claude and api_key_present():
        data_out = _claude_extract(_subset_pdf(reader, all_pages), basis)
        res.method = "claude"
        res.unit = (data_out.get("unit") or res.unit or "").lower() or None
        res.currency = data_out.get("currency") or res.currency
        res.company = data_out.get("company_name") or None
        for st in data_out.get("statements", []):
            if st.get("basis") != basis or not st.get("fiscal_years"):
                continue
            years = st["fiscal_years"]
            rows = [(r["label"], (r["values"] + [None] * len(years))[:len(years)]
                     if any(v is not None for v in r["values"]) else [])
                    for r in st["rows"]]
            sheet = f"{_STMT_NAME[st['statement']]} ({basis})"
            res.sheets[sheet] = _frame(rows, years)
    else:
        for stmt, pages in located.items():
            ptexts = [texts[p] for p in pages]
            years = _periods(ptexts[0])
            if not years:
                res.warnings.append(f"{name}: no year headers on {stmt} page {pages[0] + 1}; skipped.")
                continue
            rows = parse_statement_text(ptexts, len(years))
            res.sheets[f"{stmt} ({basis})"] = _frame(rows, years)

    if not res.sheets:
        raise PdfIngestError(f"{name}: statements located on pages {all_pages[0] + 1}-"
                             f"{all_pages[-1] + 1} but no figures could be read.")
    return res
