"""Optional Claude-assisted pre-classification of raw statement rows.

Returns a mapping of normalised label -> (category, role) that the caller merges
into the rule-based classification (rules stay as the fallback and for any label
Claude does not return).
"""

from __future__ import annotations

import json

from . import taxonomy as T
from .classify import ClassifiedRow
from .memo import MemoConfigError, api_key_present, model_name

_VALID = set(T.ALL_CATEGORIES)
_ROLES = {"value", "subtotal", "ignore"}

_SYSTEM = (
    "You classify line items from a company's financial statements into a fixed "
    "taxonomy so a credit tool can aggregate them. Reply with JSON only."
)


def _catalogue() -> str:
    return "\n".join(f"  {cid} = {T.CATEGORY_LABELS[cid]}" for cid in T.ALL_CATEGORIES)


def build_prompt(rows: list[ClassifiedRow], periods: list[str]) -> str:
    seen: dict[str, ClassifiedRow] = {}
    for r in rows:
        seen.setdefault(r.norm, r)
    lines = []
    for r in seen.values():
        sample = next((f"{p}={r.values[p]:,.0f}" for p in periods if r.values.get(p) is not None), "")
        lines.append(f'- "{r.label}"  (section: {r.section}; {sample})')
    return f"""Assign every line item below to exactly one category id and a role.

CATEGORIES:
{_catalogue()}

ROLE:
  value    = a real figure to aggregate or use
  subtotal = a reported total/subtotal to keep only for reconciliation (e.g. "Total current assets")
  ignore   = headings, per-share data, notes, blank rows

RULES:
- "Total assets", "Total liabilities", "Total equity", "Profit before tax", "Profit for the year",
  "Net cash from operating activities" and "Gross profit" are role "value".
- Only "Total current assets", "Total non-current assets", "Total current liabilities" and
  "Total non-current liabilities" are role "subtotal".
- Current portion of long-term debt and current lease liabilities -> {T.BL_STD}.
- Long-term borrowings and non-current lease liabilities -> {T.BL_LTD}.
- Depreciation & amortisation always gets its own category {T.IS_DA}, never {T.IS_OPEX}.

LINE ITEMS:
{chr(10).join(lines)}

Reply with a JSON object mapping the EXACT line-item text to {{"category": "<id>", "role": "<role>"}}.
No prose, no markdown fences."""


def llm_classify(rows: list[ClassifiedRow], periods: list[str]) -> dict[str, tuple[str, str]]:
    if not api_key_present():
        raise MemoConfigError("ANTHROPIC_API_KEY is not set - Claude pre-classification is unavailable.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise MemoConfigError("The `anthropic` package is not installed.") from exc

    client = anthropic.Anthropic()
    prompt = build_prompt(rows, periods)
    try:
        with client.messages.stream(
            model=model_name(),
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as exc:
        raise MemoConfigError(f"Claude classification call failed: {exc}") from exc

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise MemoConfigError(f"Could not parse Claude's classification response: {exc}") from exc

    label_to_norm = {}
    from .classify import normalize
    for r in rows:
        label_to_norm[r.label] = r.norm

    out: dict[str, tuple[str, str]] = {}
    for label, spec in data.items():
        if not isinstance(spec, dict):
            continue
        cat = spec.get("category")
        role = spec.get("role", "value")
        if cat not in _VALID or role not in _ROLES:
            continue
        norm = label_to_norm.get(label) or normalize(label)
        out[norm] = (cat, role)
    return out
