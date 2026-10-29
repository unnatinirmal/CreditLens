"""Rule-based credit review memo - no API key needed.

Fills the same ten-section structure the Claude memo uses, but every sentence
is driven by the computed figures: ratio readings against their strong / weak
thresholds, the score components, the multi-year trend, and stress scenarios
re-run through the same engine. Deterministic and offline; it reads more
mechanically than an analyst would, and cannot weigh qualitative factors.
"""

from __future__ import annotations

import math

from .memo import MemoContext
from .ratios import RATIO_BY_KEY, fmt
from .scoring import INVESTMENT_GRADE_MIN
from .stress import StressParams, stress_summary

_TREND_KEYS = [("EBITDA margin", "ebitda_margin"), ("Net debt / EBITDA", "net_debt_ebitda"),
               ("EBITDA / interest", "ebitda_interest"), ("FFO / debt", "ffo_debt"),
               ("Current ratio", "current_ratio")]
_COVERAGE_FLOOR = 2.0       # EBITDA / interest below this is a common distress marker


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _r(ctx: MemoContext, key: str):
    return ctx.ratios.get(key)


def _f(ctx: MemoContext, key: str) -> str:
    """Formatted ratio with its strong / adequate / weak reading."""
    d = RATIO_BY_KEY[key]
    val = ctx.ratios.get(key)
    status = d.status(val)
    tag = {"strong": "strong", "adequate": "adequate", "weak": "weak"}.get(status)
    return f"{fmt(val, d.unit)}" + (f" ({tag})" if tag else "")


def _amt(ctx: MemoContext, key: str) -> str:
    v = ctx.values.get(key)
    return "n/a" if v is None else f"{ctx.currency} {v:,.0f}"


def _label(text: str) -> str:
    """Score-component labels carry a unit suffix ("FFO / Total debt (%)") - drop it in prose."""
    return text.replace(" (%)", "")


def _round_to(x: float, step: float) -> float:
    return round(x / step) * step


def _status(ctx: MemoContext, key: str) -> str:
    return RATIO_BY_KEY[key].status(ctx.ratios.get(key))


def _direction(key: str, first, last) -> str | None:
    """'improved' / 'deteriorated' / 'stable' for a ratio between two periods."""
    if first is None or last is None:
        return None
    d = RATIO_BY_KEY[key]
    if abs(last - first) <= max(abs(first), 1e-9) * 0.05:
        return "stable"
    better = last > first if d.direction == "higher" else last < first
    return "improved" if better else "deteriorated"


def _scenario_drivers(p: StressParams) -> str:
    parts = []
    if p.revenue_decline_pct:
        parts.append(f"revenue -{p.revenue_decline_pct:.0f}%")
    if p.ebitda_decline_pct:
        parts.append(f"EBITDA -{p.ebitda_decline_pct:.0f}%")
    if p.ebitda_margin_drop_pp:
        parts.append(f"margin -{p.ebitda_margin_drop_pp:.1f}pp")
    if p.rate_shock_bps:
        parts.append(f"rates +{p.rate_shock_bps:.0f}bps")
    if p.capex_change_pct:
        parts.append(f"capex {p.capex_change_pct:+.0f}%")
    if p.incremental_debt:
        parts.append(f"+{p.incremental_debt:,.0f} new debt")
    return ", ".join(parts) or "no change"


def _breaking_point(ctx: MemoContext, test) -> int | None:
    """Smallest EBITDA decline (%, 5-point steps) at which ``test(summary)`` is true."""
    for pct in range(5, 100, 5):
        if test(stress_summary(ctx.values, StressParams(ebitda_decline_pct=pct))):
            return pct
    return None


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def _recommendation(ctx: MemoContext, stressed_ig: bool) -> str:
    s = ctx.score
    if s.coverage < 0.6:
        return "Defer - key inputs are missing; complete the data set before taking a credit view"
    if s.composite >= 74 and stressed_ig:
        return "Approve"
    if s.investment_grade:
        return ("Approve with standard covenants" if stressed_ig
                else "Approve with conditions - covenant headroom is thin under a downturn")
    if s.composite >= 54:
        return "Approve with conditions - tighter covenants, security and closer monitoring"
    if s.composite >= 42:
        return "Refer - only with strong structural protection (security, amortisation, guarantees)"
    return "Decline"


def _drivers(ctx: MemoContext) -> tuple[list[str], list[str]]:
    comps = [c for c in ctx.score.components if c.subscore is not None]
    comps.sort(key=lambda c: c.subscore, reverse=True)
    unit = lambda c: RATIO_BY_KEY[c.ratio_key].unit  # noqa: E731
    strengths = [f"{_label(c.label)} of {fmt(c.value, unit(c))} (sub-score {c.subscore:.0f}/100)"
                 for c in comps[:2] if c.subscore >= 60]
    weaknesses = [f"{_label(c.label)} of {fmt(c.value, unit(c))} (sub-score {c.subscore:.0f}/100)"
                  for c in comps[::-1][:2] if c.subscore < 60]
    return strengths, weaknesses


def _trend_sentence(ctx: MemoContext) -> str:
    h = ctx.history or []
    if len(h) < 2:
        return "Only one period was supplied, so no trend can be assessed."
    first, last = h[0], h[-1]
    moves = []
    for label, key in _TREND_KEYS:
        a, b = first["ratios"].get(key), last["ratios"].get(key)
        d = _direction(key, a, b)
        if d and d != "stable":
            u = RATIO_BY_KEY[key].unit
            moves.append(f"{label} {d} from {fmt(a, u)} to {fmt(b, u)}")
    score_move = last["score"].composite - first["score"].composite
    head = (f"Between {first['period']} and {last['period']} the model score moved "
            f"{score_move:+.0f} points ({first['score'].rating} -> {last['score'].rating}).")
    return head + (" " + "; ".join(moves) + "." if moves else " Key ratios were broadly stable.")


def _risks(ctx: MemoContext, worst_name: str | None, worst_ig: bool) -> list[str]:
    risks = []
    order = ["net_debt_ebitda", "ebitda_interest", "ffo_debt", "fcf_debt", "debt_capital",
             "current_ratio", "quick_ratio", "ebitda_margin", "cfo_debt", "roce"]
    for key in order:
        if _status(ctx, key) == "weak":
            d = RATIO_BY_KEY[key]
            risks.append(f"**Weak {d.label}** at {fmt(_r(ctx, key), d.unit)} "
                         f"(weak threshold {fmt(d.weak, d.unit)}).")
    h = ctx.history or []
    if len(h) >= 2:
        for label, key in _TREND_KEYS:
            if _direction(key, h[0]["ratios"].get(key), h[-1]["ratios"].get(key)) == "deteriorated":
                risks.append(f"**Deteriorating {label}** over {h[0]['period']}-{h[-1]['period']}.")
    if worst_name and ctx.score.investment_grade and not worst_ig:
        risks.append(f"**Downturn sensitivity** - the '{worst_name}' scenario takes the credit below "
                     "investment grade.")
    if ctx.score.coverage < 0.85:
        risks.append(f"**Data gaps** - only {ctx.score.coverage:.0%} of the scoring model's inputs "
                     "were available, which lowers confidence in the score.")
    return risks


def _covenants(ctx: MemoContext) -> list[str]:
    out = []
    nde = _r(ctx, "net_debt_ebitda")
    if nde is not None:
        level = max(2.5, _round_to(max(nde, 0) + 1.0, 0.25))
        out.append(f"Maximum net debt / EBITDA of **{level:.2f}x** (currently {fmt(nde, 'x')}).")
    cov = _r(ctx, "ebitda_interest")
    if cov is not None:
        level = max(_COVERAGE_FLOOR, _round_to(cov * 0.6, 0.25))
        level = min(level, 6.0)
        out.append(f"Minimum EBITDA / interest of **{level:.2f}x** (currently {fmt(cov, 'x')}).")
    cr = _r(ctx, "current_ratio")
    if cr is not None:
        level = max(1.0, min(_round_to(cr * 0.8, 0.05), 1.5))
        out.append(f"Minimum current ratio of **{level:.2f}x** (currently {fmt(cr, 'x')}).")
    if (ctx.values.get("dividends_paid") or 0) > 0 and _status(ctx, "fcf_debt") != "strong":
        out.append("Distributions permitted only while leverage is inside covenant with headroom.")
    out.append("Quarterly compliance certificate; annual audited accounts within 120 days of year-end.")
    out.append(f"Monitoring trigger: model score below {INVESTMENT_GRADE_MIN:.0f} "
               "(sub-investment-grade) or a one-notch rating migration prompts a full review.")
    return out


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def generate_template_memo(ctx: MemoContext) -> str:
    v, s = ctx.values, ctx.score
    scen = [(name, p, stress_summary(v, p)) for name, p in ctx.scenarios]
    worst = min(scen, key=lambda t: t[2]["score"].composite) if scen else None
    worst_ig = worst is None or worst[2]["score"].composite >= INVESTMENT_GRADE_MIN
    # "stressed investment grade" = still IG under the middle (moderate) preset
    mid = scen[len(scen) // 2] if scen else None
    stressed_ig = mid is None or mid[2]["score"].composite >= INVESTMENT_GRADE_MIN

    rec = _recommendation(ctx, stressed_ig)
    strengths, weaknesses = _drivers(ctx)
    unit_note = f"{ctx.currency} {ctx.unit_label}".strip()
    out: list[str] = []
    add = out.append

    add(f"# Credit Review Memo - {ctx.company}")
    add(f"*Period: {ctx.period} · Sector: {ctx.industry or 'not specified'} · Figures in {unit_note} · "
        "Rule-based draft generated by CreditLens from the computed metrics.*")

    # 1 ----------------------------------------------------------------------
    add("## 1. Executive Summary & Recommendation")
    add(f"**Recommendation: {rec}.** Indicative internal rating **{s.rating}** "
        f"(model score {s.composite:.0f}/100, {s.risk.lower()} risk; "
        f"{'investment grade' if s.investment_grade else 'sub-investment grade'}; "
        f"data confidence {s.confidence.lower()}).")
    if strengths:
        add("- Supported by " + " and ".join(strengths) + ".")
    if weaknesses:
        add("- Constrained by " + " and ".join(weaknesses) + ".")
    if worst:
        ws = worst[2]["score"]
        add(f"- Under the most severe preset ('{worst[0]}') the score falls to {ws.composite:.0f} "
            f"({ws.rating}).")
    add("- " + _trend_sentence(ctx))

    # 2 ----------------------------------------------------------------------
    add("## 2. Business & Financial Profile")
    add(f"Revenue of {_amt(ctx, 'revenue')} generated EBITDA of {_amt(ctx, 'ebitda')} "
        f"(EBITDA margin {_f(ctx, 'ebitda_margin')}) and net income of {_amt(ctx, 'net_income')} "
        f"(net margin {_f(ctx, 'net_margin')}). Total assets are {_amt(ctx, 'total_assets')} "
        f"against equity of {_amt(ctx, 'total_equity')}; return on capital employed is "
        f"{_f(ctx, 'roce')} and return on equity {_f(ctx, 'roe')}.")

    # 3 ----------------------------------------------------------------------
    add("## 3. Leverage & Capital Structure")
    nd = v.get("net_debt")
    if nd is not None and nd < 0:
        add(f"The company is in a **net cash** position: cash and liquid investments exceed total debt "
            f"of {_amt(ctx, 'total_debt')} by {ctx.currency} {-nd:,.0f}.")
    else:
        add(f"Total debt is {_amt(ctx, 'total_debt')} and net debt {_amt(ctx, 'net_debt')}, giving "
            f"net debt / EBITDA of {_f(ctx, 'net_debt_ebitda')} and total debt / EBITDA of "
            f"{_f(ctx, 'debt_ebitda')}.")
    add(f"Debt / capital is {_f(ctx, 'debt_capital')} and debt / equity {_f(ctx, 'debt_equity')}. "
        "Maturity profile, security and debt pricing are not in the statements and should be "
        "obtained from the debt schedule.")

    # 4 ----------------------------------------------------------------------
    add("## 4. Coverage & Debt Service")
    cov = _r(ctx, "ebitda_interest")
    add(f"EBITDA / interest is {_f(ctx, 'ebitda_interest')}, EBIT / interest {_f(ctx, 'ebit_interest')} "
        f"and FFO / debt {_f(ctx, 'ffo_debt')}. Debt-service coverage is {_f(ctx, 'dscr')}.")
    if cov is not None and cov > _COVERAGE_FLOOR:
        headroom = (1 - _COVERAGE_FLOOR / cov) * 100
        add(f"EBITDA could fall by about **{headroom:.0f}%** before interest cover reaches "
            f"{_COVERAGE_FLOOR:.1f}x.")
    elif cov is not None:
        add(f"Interest cover is already at or below {_COVERAGE_FLOOR:.1f}x - debt service is "
            "vulnerable to any earnings shortfall.")

    # 5 ----------------------------------------------------------------------
    add("## 5. Liquidity")
    add(f"Current ratio {_f(ctx, 'current_ratio')}, quick ratio {_f(ctx, 'quick_ratio')} and cash "
        f"ratio {_f(ctx, 'cash_ratio')}. Working capital is {_amt(ctx, 'working_capital')}, with "
        f"cash and short-term investments of {ctx.currency} {v.get('_cash_like') or 0:,.0f}. "
        "Undrawn committed facilities are not visible in the statements and should be confirmed.")

    # 6 ----------------------------------------------------------------------
    add("## 6. Cash Flow")
    cfo, fcf = v.get("cfo"), v.get("fcf")
    if cfo is None:
        add("No cash-flow statement figures were available; cash-flow quality cannot be assessed.")
    else:
        add(f"Operating cash flow of {_amt(ctx, 'cfo')} (CFO / revenue {_f(ctx, 'cfo_margin')}) "
            f"funded capex of {_amt(ctx, 'capex')}, leaving free cash flow of {_amt(ctx, 'fcf')} "
            f"(FCF / debt {_f(ctx, 'fcf_debt')}).")
        if v.get("dividends_paid"):
            add(f"Dividends paid were {_amt(ctx, 'dividends_paid')}; FCF after dividends was "
                f"{ctx.currency} {v.get('fcf_after_div') or 0:,.0f}.")
        debt = v.get("total_debt") or 0
        if fcf and fcf > 0 and debt > 0:
            yrs = math.ceil(debt / fcf)
            add("At the current run-rate, free cash flow would repay total debt "
                + ("**within a year**." if yrs <= 1 else f"in about **{yrs} years**."))
        elif fcf is not None and fcf <= 0:
            add("Free cash flow is negative - growth or debt service depends on external funding.")

    # 7 ----------------------------------------------------------------------
    add("## 7. Scenario & Stress Analysis")
    base = stress_summary(v, StressParams())
    rows = ["| Scenario | Shocks | Score | Rating | Net debt/EBITDA | EBITDA/interest |",
            "|---|---|---|---|---|---|",
            f"| Base case | none | {base['score'].composite:.0f} | {base['score'].rating} | "
            f"{fmt(base['ratios'].get('net_debt_ebitda'), 'x')} | "
            f"{fmt(base['ratios'].get('ebitda_interest'), 'x')} |"]
    for name, p, res in scen:
        rows.append(f"| {name} | {_scenario_drivers(p)} | {res['score'].composite:.0f} | "
                    f"{res['score'].rating} | {fmt(res['ratios'].get('net_debt_ebitda'), 'x')} | "
                    f"{fmt(res['ratios'].get('ebitda_interest'), 'x')} |")
    add("\n".join(rows))
    if s.investment_grade:
        bp = _breaking_point(ctx, lambda r: r["score"].composite < INVESTMENT_GRADE_MIN)
        add(f"The credit falls below investment grade at an EBITDA decline of about **{bp}%**."
            if bp else "The credit remains investment grade even under a 95% EBITDA decline in this model.")
    if scen:
        singles = [("revenue decline", StressParams(revenue_decline_pct=15)),
                   ("margin compression", StressParams(ebitda_margin_drop_pp=3)),
                   ("rate shock", StressParams(rate_shock_bps=300))]
        hits = [(lbl, base["score"].composite - stress_summary(v, p)["score"].composite)
                for lbl, p in singles]
        lbl, hit = max(hits, key=lambda t: t[1])
        add(f"Of comparable single shocks (revenue -15%, margin -3pp, rates +300bps), the "
            f"**{lbl}** is the most damaging, costing {hit:.0f} score points.")

    # 8 ----------------------------------------------------------------------
    add("## 8. Key Risks & Mitigants")
    risks = _risks(ctx, worst[0] if worst else None, worst_ig)
    if risks:
        add("\n".join(f"- {r}" for r in risks))
    else:
        add("- No ratio screens as weak in the period reviewed.")
    mitigants = [f"strong {RATIO_BY_KEY[k].label} ({fmt(_r(ctx, k), RATIO_BY_KEY[k].unit)})"
                 for k in ("net_debt_ebitda", "ebitda_interest", "current_ratio", "fcf_debt", "equity_ratio")
                 if _status(ctx, k) == "strong"]
    if mitigants:
        add("Mitigants: " + "; ".join(mitigants) + ".")
    if ctx.analyst_notes.strip():
        add(f"Analyst context: {ctx.analyst_notes.strip()}")

    # 9 ----------------------------------------------------------------------
    add("## 9. Covenants & Monitoring")
    add("\n".join(f"- {c}" for c in _covenants(ctx)))

    # 10 ---------------------------------------------------------------------
    add("## 10. Rating Rationale")
    table = ["| Factor | Value | Sub-score | Weight |", "|---|---|---|---|"]
    for c in s.components:
        u = RATIO_BY_KEY[c.ratio_key].unit
        table.append(f"| {_label(c.label)} | {fmt(c.value, u)} | "
                     f"{'n/a' if c.subscore is None else f'{c.subscore:.0f}'} | {c.raw_weight:.0%} |")
    add("\n".join(table))
    add(f"The weighted composite of {s.composite:.0f}/100 maps to **{s.rating}** ({s.risk.lower()} "
        f"risk), with {s.coverage:.0%} of model inputs available ({s.confidence.lower()} confidence). "
        "This is a screening model with illustrative calibrations, not an agency methodology; the "
        "recommendation above should be read alongside qualitative factors - industry position, "
        "management, ownership and debt documentation - which this memo does not assess.")

    return "\n\n".join(out)
