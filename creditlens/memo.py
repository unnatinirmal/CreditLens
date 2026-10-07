"""Claude-generated credit review memo.

Builds a structured analyst-style prompt from the computed metrics and asks
Claude to draft a credit assessment. The API key is read from the environment
(``ANTHROPIC_API_KEY``); the model can be overridden with ``CREDITLENS_MODEL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .ratios import RATIO_BY_KEY, by_category, fmt
from .scoring import CreditScore
from .stress import StressParams, stress_summary

DEFAULT_MODEL = "claude-opus-5"
SYSTEM_PROMPT = (
    "You are a senior corporate credit analyst writing an internal credit review "
    "memo for a lending / fixed-income committee. You are precise, evidence-led and "
    "balanced: you quantify every claim with the metrics provided, call out data "
    "gaps, and never invent figures that are not in the input. Your tone is concise "
    "and professional, matching a bank or rating-agency credit note."
)


class MemoConfigError(RuntimeError):
    """Raised when the Claude API is not usable (missing key / SDK)."""


@dataclass
class MemoContext:
    company: str
    period: str
    currency: str
    unit_label: str
    industry: str
    values: dict
    ratios: dict
    score: CreditScore
    base_params: StressParams
    scenarios: list[tuple[str, StressParams]]
    analyst_notes: str = ""
    history: list[dict] = None  # optional: [{"period","ratios","score"}] oldest->newest


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name() -> str:
    return os.environ.get("CREDITLENS_MODEL", DEFAULT_MODEL)


def _num(v, currency="") -> str:
    if v is None:
        return "n/a"
    return f"{currency + ' ' if currency else ''}{v:,.1f}"


def _ratio_block(ratios: dict) -> str:
    lines = []
    for cat, defs in by_category().items():
        lines.append(f"\n{cat}:")
        for d in defs:
            val = ratios.get(d.key)
            status = d.status(val)
            tag = "" if status in ("na", "adequate") else f"  [{status}]"
            lines.append(f"  - {d.label}: {fmt(val, d.unit)}{tag}")
    return "\n".join(lines)


def _scenario_block(ctx: MemoContext) -> str:
    out = []
    base = stress_summary(ctx.values, StressParams())
    out.append(
        f"  - Base case: score {base['score'].composite:.0f} "
        f"({base['score'].rating}), net debt/EBITDA "
        f"{fmt(base['ratios'].get('net_debt_ebitda'), 'x')}, EBITDA/interest "
        f"{fmt(base['ratios'].get('ebitda_interest'), 'x')}")
    for name, p in ctx.scenarios:
        res = stress_summary(ctx.values, p)
        s = res["score"]
        drivers = []
        if p.revenue_decline_pct:
            drivers.append(f"revenue -{p.revenue_decline_pct:.0f}%")
        if p.ebitda_decline_pct:
            drivers.append(f"EBITDA -{p.ebitda_decline_pct:.0f}%")
        if p.ebitda_margin_drop_pp:
            drivers.append(f"margin -{p.ebitda_margin_drop_pp:.1f}pp")
        if p.rate_shock_bps:
            drivers.append(f"rates +{p.rate_shock_bps:.0f}bps")
        if p.capex_change_pct:
            drivers.append(f"capex {p.capex_change_pct:+.0f}%")
        if p.incremental_debt:
            drivers.append(f"+{p.incremental_debt:,.0f} new debt")
        out.append(
            f"  - {name} ({', '.join(drivers) or 'no change'}): score "
            f"{s.composite:.0f} ({s.rating}, {s.risk} risk), net debt/EBITDA "
            f"{fmt(res['ratios'].get('net_debt_ebitda'), 'x')}, EBITDA/interest "
            f"{fmt(res['ratios'].get('ebitda_interest'), 'x')}, FCF/debt "
            f"{fmt(res['ratios'].get('fcf_debt'), '%')}")
    return "\n".join(out)


def _history_block(ctx: MemoContext) -> str:
    if not ctx.history or len(ctx.history) < 2:
        return "  (single period supplied - no trend available)"
    keys = [("EBITDA margin", "ebitda_margin", "%"), ("Net debt/EBITDA", "net_debt_ebitda", "x"),
            ("EBITDA/interest", "ebitda_interest", "x"), ("FFO/debt", "ffo_debt", "%"),
            ("FCF/debt", "fcf_debt", "%"), ("Current ratio", "current_ratio", "x")]
    lines = []
    for h in ctx.history:
        parts = [f"{lbl} {fmt(h['ratios'].get(k), u)}" for lbl, k, u in keys]
        lines.append(f"  - {h['period']}: score {h['score'].composite:.0f} ({h['score'].rating}); "
                     + ", ".join(parts))
    return "\n".join(lines)


def build_prompt(ctx: MemoContext) -> str:
    v = ctx.values
    sc = ctx.score
    key_figures = "\n".join(
        f"  - {label}: {_num(v.get(key), ctx.currency)}"
        for label, key in [
            ("Revenue", "revenue"), ("EBITDA", "ebitda"), ("EBIT", "ebit"),
            ("Net income", "net_income"), ("Interest expense", "interest_expense"),
            ("Total debt", "total_debt"), ("Net debt", "net_debt"),
            ("Cash & equivalents", "cash"), ("Total equity", "total_equity"),
            ("Total assets", "total_assets"), ("CFO", "cfo"),
            ("Capex", "capex"), ("Free cash flow", "fcf"),
        ])
    comp_lines = "\n".join(
        f"  - {c.label}: value {fmt(c.value, RATIO_BY_KEY[c.ratio_key].unit)}, "
        f"sub-score {c.subscore:.0f}/100 (weight {c.raw_weight:.0%})"
        if c.subscore is not None else
        f"  - {c.label}: no data"
        for c in sc.components)

    return f"""Draft a structured corporate credit review memo for the following borrower.

COMPANY: {ctx.company}
REPORTING PERIOD: {ctx.period}
INDUSTRY / SECTOR: {ctx.industry or "not specified"}
REPORTING UNIT: {ctx.unit_label} ({ctx.currency})

KEY FINANCIAL FIGURES:
{key_figures}

MULTI-YEAR TREND (oldest to newest):
{_history_block(ctx)}

FULL RATIO SET for {ctx.period} (status tags flag readings that screen as strong or weak):
{_ratio_block(ctx.ratios)}

MODEL CREDIT SCORE (transparent weighted screening model, 0-100, higher = stronger):
  - Composite score: {sc.composite:.0f}/100
  - Indicative rating band: {sc.rating}
  - Risk category: {sc.risk}
  - Investment grade on this model: {"yes" if sc.investment_grade else "no"}
  - Data coverage / confidence: {sc.coverage:.0%} / {sc.confidence}
  Component sub-scores:
{comp_lines}

SCENARIO STRESS TESTS:
{_scenario_block(ctx)}

ANALYST NOTES (optional context, may be blank):
{ctx.analyst_notes or "none provided"}

Write the memo with these sections, using markdown headings (##):
1. Executive Summary & Recommendation - a clear credit opinion (e.g. approve / approve with conditions / decline / monitor), indicative internal rating, and the 3-4 points that drive it.
2. Business & Financial Profile - scale, profitability and margin trend, asset base.
3. Leverage & Capital Structure - debt quantum, leverage vs. the model anchors, structure and maturity considerations you can infer.
4. Coverage & Debt Service - interest and fixed-charge coverage, headroom.
5. Liquidity - current/quick/cash positions, working capital, near-term sources vs. uses.
6. Cash Flow - CFO/FCF quality, capex intensity, distribution policy, deleveraging capacity.
7. Scenario & Stress Analysis - interpret the stress results above; identify the point at which the credit migrates to sub-investment-grade or coverage falls below ~2x; name the most damaging single driver.
8. Key Risks & Mitigants - bullet list, most material first.
9. Covenants & Monitoring - suggested financial covenants (with indicative levels tied to the figures above) and monitoring triggers.
10. Rating Rationale - reconcile your opinion with the model score, noting where you diverge and why.

Keep the whole memo under ~900 words. Do not fabricate figures beyond what is given; where an input is missing, say so and state what you would request."""


def generate_memo(ctx: MemoContext, *, max_tokens: int = 8000) -> str:
    if not api_key_present():
        raise MemoConfigError(
            "ANTHROPIC_API_KEY is not set. Add it to your environment or to "
            "`.streamlit/secrets.toml` and restart the app.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise MemoConfigError(
            "The `anthropic` package is not installed. Run `pip install anthropic`."
        ) from exc

    client = anthropic.Anthropic()
    prompt = build_prompt(ctx)
    try:
        with client.messages.stream(
            model=model_name(),
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:  # surface a clean message to the UI
        raise MemoConfigError(f"Claude API call failed: {exc}") from exc

    parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    return text or "_Claude returned an empty response._"
