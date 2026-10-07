"""CreditLens - automated credit risk assessment & scenario stress-tester.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import hashlib

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

try:  # optional - lets users keep the key in a local .env
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

from creditlens import __version__, charts
from creditlens.classify import aggregate, classify_rows, reconciliation
from creditlens.classify_llm import llm_classify
from creditlens.memo import (
    MemoConfigError,
    MemoContext,
    api_key_present,
    generate_memo,
    model_name,
)
from creditlens.model import derive
from creditlens.parser import extract_files
from creditlens.ratios import RATIO_BY_KEY, by_category, compute_all, fmt
from creditlens.sample import (
    SAMPLE_COMPANY,
    SAMPLE_INDUSTRY,
    SAMPLE_UNIT,
    blank_canonical_periods,
    blank_template_xlsx,
    sample_workbook_xlsx,
)
from creditlens.schema import LINE_ITEMS
from creditlens.scoring import RATING_BANDS, score_credit
from creditlens import taxonomy as T
from creditlens.stress import (
    HEATMAP_METRIC_NAMES,
    StressParams,
    heatmap_frame,
    metric_lower_is_better,
    stress_summary,
)

matplotlib.use("Agg")
st.set_page_config(page_title="CreditLens", page_icon="🔎", layout="wide")

STATUS_ICON = {"strong": "🟢", "adequate": "🟡", "weak": "🔴", "na": "▫️"}


def _amt(v, dp: int = 0) -> str:
    return "n/a" if v is None else f"{v:,.{dp}f}"


CAT_OPTIONS = [T.CATEGORY_LABELS[c] for c in T.ALL_CATEGORIES]
LABEL_TO_CAT = {T.CATEGORY_LABELS[c]: c for c in T.ALL_CATEGORIES}
ROLE_OPTIONS = ["value", "subtotal", "ignore"]

PRESET_SCENARIOS: list[tuple[str, StressParams]] = [
    ("Mild slowdown", StressParams(revenue_decline_pct=5, ebitda_margin_drop_pp=1.0, rate_shock_bps=100)),
    ("Moderate downturn", StressParams(revenue_decline_pct=12, ebitda_margin_drop_pp=2.5, rate_shock_bps=250)),
    ("Severe recession", StressParams(revenue_decline_pct=22, ebitda_margin_drop_pp=4.0, rate_shock_bps=400,
                                      capex_change_pct=-15)),
]


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("mode", None)          # "upload" | "manual"
    ss.setdefault("rows", [])            # list[ClassifiedRow]
    ss.setdefault("periods", [])         # list[Period]
    ss.setdefault("overrides", {})       # norm-label -> (category, role)
    ss.setdefault("warnings", [])
    ss.setdefault("manual_periods", {})  # period -> canonical dict
    ss.setdefault("company", "")
    ss.setdefault("industry", "")
    ss.setdefault("currency", "USD")
    ss.setdefault("unit_label", "millions")
    ss.setdefault("memo_cache", {})


def _load_sample() -> None:
    rows, periods, warns = extract_files([("northwind_sample.xlsx", sample_workbook_xlsx())])
    st.session_state.update(
        mode="upload", rows=rows, periods=periods, overrides={}, warnings=warns,
        company=SAMPLE_COMPANY, industry=SAMPLE_INDUSTRY, currency="USD", unit_label=SAMPLE_UNIT)


def _start_manual() -> None:
    if not st.session_state.manual_periods:
        st.session_state.manual_periods = blank_canonical_periods(["FY2023", "FY2024"])
    st.session_state.mode = "manual"


# --------------------------------------------------------------------------- #
# sidebar - data
# --------------------------------------------------------------------------- #
def sidebar_data() -> None:
    st.sidebar.title("🔎 CreditLens")
    st.sidebar.caption(f"v{__version__} · credit risk & stress-testing")

    st.sidebar.subheader("1 · Financial statements")
    ups = st.sidebar.file_uploader(
        "Upload annual-report statements (.xlsx / .csv). Multiple files are merged by year.",
        type=["xlsx", "xls", "csv"], accept_multiple_files=True)
    if ups and st.sidebar.button("Process files", width="stretch", type="primary"):
        files = [(f.name, f.getvalue()) for f in ups]
        rows, periods, warns = extract_files(files)
        if not periods:
            st.sidebar.error("Could not find any period columns. " + " ".join(warns))
        else:
            st.session_state.update(mode="upload", rows=rows, periods=periods,
                                    overrides={}, warnings=warns)
            st.sidebar.success(f"{len(rows)} line items · {len(periods)} year(s).")

    c1, c2 = st.sidebar.columns(2)
    c1.button("Load sample", width="stretch", on_click=_load_sample)
    c2.button("Manual entry", width="stretch", on_click=_start_manual)
    st.sidebar.download_button("Canonical template (.xlsx)", blank_template_xlsx(),
                               file_name="creditlens_template.xlsx", width="stretch")

    st.sidebar.subheader("2 · Borrower")
    st.session_state.company = st.sidebar.text_input("Company name", st.session_state.company)
    st.session_state.industry = st.sidebar.text_input("Industry / sector", st.session_state.industry)
    cc1, cc2 = st.sidebar.columns(2)
    st.session_state.currency = cc1.text_input("Currency", st.session_state.currency)
    st.session_state.unit_label = cc2.text_input("Units", st.session_state.unit_label)

    st.sidebar.caption("Claude memo: " + ("✅ key detected · `%s`" % model_name()
                                          if api_key_present() else "⚠️ set ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- #
# compute pipeline
# --------------------------------------------------------------------------- #
def _periods_to_frame(pdata: dict[str, dict]) -> pd.DataFrame:
    cols = list(pdata)
    rows = [[li.label] + [pdata[p].get(li.key) for p in cols] for li in LINE_ITEMS]
    return pd.DataFrame(rows, columns=["Line item"] + cols).set_index("Line item")


def _frame_to_periods(df: pd.DataFrame) -> dict[str, dict]:
    label_to_key = {li.label: li.key for li in LINE_ITEMS}
    out: dict[str, dict] = {c: {} for c in df.columns}
    for label, row in df.iterrows():
        key = label_to_key.get(label)
        if not key:
            continue
        for c in df.columns:
            out[c][key] = None if pd.isna(row[c]) else float(row[c])
    return out


def compute():
    ss = st.session_state
    if ss.mode == "manual":
        pdata = {p: dict(v) for p, v in ss.manual_periods.items()}
        plabels = list(pdata)
        agg = None
    else:
        plabels = [p.label for p in ss.periods]
        classify_rows(ss.rows, plabels, ss.overrides)
        agg = aggregate(ss.rows, plabels)
        pdata = agg.values
    derived = {p: derive(pdata[p]) for p in plabels}
    ratios = {p: compute_all(derived[p]) for p in plabels}
    scores = {p: score_credit(ratios[p]) for p in plabels}
    return plabels, pdata, derived, ratios, scores, agg


# --------------------------------------------------------------------------- #
# sidebar - view controls
# --------------------------------------------------------------------------- #
def sidebar_view(plabels: list[str]):
    st.sidebar.subheader("3 · View")
    focus = st.sidebar.selectbox("Focus year", plabels, index=len(plabels) - 1)
    compare = st.sidebar.checkbox("Comparison mode", value=len(plabels) > 1,
                                  disabled=len(plabels) < 2)
    if compare and len(plabels) >= 2:
        maxn = min(5, len(plabels))
        n = st.sidebar.select_slider("Years to compare", options=list(range(2, maxn + 1)), value=maxn)
        comp_years = plabels[-int(n):]
    else:
        comp_years = [focus]
    return focus, compare, comp_years


# --------------------------------------------------------------------------- #
# tab: statements  (raw data + classification review, or manual editor)
# --------------------------------------------------------------------------- #
def tab_statements(plabels, derived, agg):
    ss = st.session_state
    if ss.mode == "manual":
        st.subheader("Manual line-item entry")
        cur = st.text_input("Periods (comma-separated, oldest first)",
                            ", ".join(ss.manual_periods))
        names = [p.strip() for p in cur.split(",") if p.strip()]
        if names and names != list(ss.manual_periods):
            ss.manual_periods = {n: ss.manual_periods.get(n, {li.key: None for li in LINE_ITEMS})
                                 for n in names}
            st.rerun()
        df = _periods_to_frame(ss.manual_periods)
        edited = st.data_editor(
            df, width="stretch", num_rows="fixed",
            column_config={c: st.column_config.NumberColumn(c, format="%.1f") for c in df.columns})
        ss.manual_periods = _frame_to_periods(edited)
        return

    st.subheader("Extracted line items & classification")
    st.caption("Every row was auto-mapped to a category. Correct any mapping in the grid — "
               "ratios recompute immediately. `subtotal` rows are kept for reconciliation only; "
               "`ignore` drops the row.")

    b1, b2, b3 = st.columns([1, 1, 3])
    if b1.button("↺ Reset to auto", width="stretch"):
        ss.overrides = {}
        st.rerun()
    if b2.button("✨ Pre-classify with Claude", width="stretch", disabled=not api_key_present()):
        try:
            with st.spinner(f"Classifying with {model_name()}…"):
                ss.overrides.update(llm_classify(ss.rows, plabels))
            st.rerun()
        except MemoConfigError as exc:
            st.error(str(exc))
    n_over = sum(1 for r in ss.rows if r.norm in ss.overrides)
    b3.caption(f"{len(ss.rows)} rows · {n_over} overridden · "
               f"{sum(1 for r in ss.rows if r.role == 'value' and r.category != T.IGNORE)} feed the model")

    if ss.warnings:
        with st.expander("Parser warnings"):
            for w in ss.warnings:
                st.write("• " + w)

    grid = pd.DataFrame(
        [{"Line item": r.label, "Section": r.section,
          "Category": T.CATEGORY_LABELS.get(r.category, r.category), "Role": r.role,
          **{pl: r.values.get(pl) for pl in plabels}} for r in ss.rows],
        index=[r.norm for r in ss.rows])
    edited = st.data_editor(
        grid, width="stretch", hide_index=True, num_rows="fixed",
        disabled=["Line item", "Section"] + plabels,
        column_config={
            "Category": st.column_config.SelectboxColumn(options=CAT_OPTIONS, required=True),
            "Role": st.column_config.SelectboxColumn(options=ROLE_OPTIONS, required=True),
            **{pl: st.column_config.NumberColumn(pl, format="%.1f") for pl in plabels}},
        key="classgrid")

    by_norm = {r.norm: r for r in ss.rows}
    changed = False
    for norm, erow in edited.iterrows():
        new_cat = LABEL_TO_CAT.get(erow["Category"])
        new_role = erow["Role"]
        cur = by_norm.get(norm)
        if cur and new_cat and (new_cat != cur.category or new_role != cur.role):
            ss.overrides[norm] = (new_cat, new_role)
            changed = True
    if changed:
        st.rerun()

    st.divider()
    st.markdown("**Reconciliation** — classified aggregates vs. the totals reported in the file")
    rec = pd.DataFrame(reconciliation(agg, derived, plabels))

    def _hl(row):
        d = row["Difference"]
        bad = d is not None and abs(d) > 0.01 * (abs(row["Classified"] or 0) + 1)
        return ["background-color: #fdecea" if bad else "" for _ in row]

    st.dataframe(rec.style.apply(_hl, axis=1).format(
        {"Reported": "{:,.1f}", "Classified": "{:,.1f}", "Difference": "{:,.1f}"}, na_rep="—"),
        width="stretch", hide_index=True)


# --------------------------------------------------------------------------- #
# tab: overview
# --------------------------------------------------------------------------- #
def _series(ratios, years, key):
    return [ratios[y].get(key) for y in years]


def _vseries(derived, years, key):
    return [derived[y].get(key) for y in years]


def tab_overview(plabels, derived, ratios, scores, focus, comp_years):
    cur = ratios[focus]
    sc = scores[focus]
    k = st.columns(6)
    k[0].metric("Revenue", _amt(derived[focus].get("revenue")))
    k[1].metric("EBITDA margin", fmt(cur.get("ebitda_margin"), "%"))
    k[2].metric("Net debt / EBITDA", fmt(cur.get("net_debt_ebitda"), "x"))
    k[3].metric("EBITDA / interest", fmt(cur.get("ebitda_interest"), "x"))
    k[4].metric("Current ratio", fmt(cur.get("current_ratio"), "x"))
    k[5].metric("Credit score", f"{sc.composite:.0f}", sc.rating)
    st.caption(f"Focus year **{focus}** · rating **{sc.rating}** ({sc.risk} risk) · "
               f"data coverage {sc.coverage:.0%}")
    st.divider()

    if len(comp_years) > 1:
        years = comp_years
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Revenue & EBITDA margin**")
            st.pyplot(charts.revenue_profit_trend(
                years, _vseries(derived, years, "revenue"),
                _series(ratios, years, "ebitda_margin"), st.session_state.currency))
            st.markdown("**Capital structure**")
            st.pyplot(charts.stacked_bars(years, {
                "Equity": _vseries(derived, years, "total_equity"),
                "Short-term debt": _vseries(derived, years, "short_term_debt"),
                "Long-term debt": _vseries(derived, years, "long_term_debt"),
            }, ylabel=st.session_state.currency))
            st.markdown("**Credit score trend**")
            st.pyplot(charts.score_trend(years, [scores[y].composite for y in years], RATING_BANDS))
        with c2:
            st.markdown("**Margin trend**")
            st.pyplot(charts.multi_line(years, {
                "Gross": _series(ratios, years, "gross_margin"),
                "EBITDA": _series(ratios, years, "ebitda_margin"),
                "EBIT": _series(ratios, years, "ebit_margin"),
                "Net": _series(ratios, years, "net_margin"),
            }, ylabel="%"))
            st.markdown("**Leverage vs. coverage**")
            st.pyplot(charts.multi_line(years, {
                "Net debt / EBITDA (x)": _series(ratios, years, "net_debt_ebitda"),
                "EBITDA / interest (x)": _series(ratios, years, "ebitda_interest"),
                "FFO / debt (%)": _series(ratios, years, "ffo_debt"),
            }, ylabel="x  /  %"))
            st.markdown("**Cash flow**")
            st.pyplot(charts.grouped_bars(years, {
                "CFO": _vseries(derived, years, "cfo"),
                "Capex": _vseries(derived, years, "capex"),
                "FCF": _vseries(derived, years, "fcf"),
            }, ylabel=st.session_state.currency))
    else:
        d = derived[focus]
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**Profitability bridge**")
            st.pyplot(charts.waterfall([
                ("Revenue", d.get("revenue")),
                ("- COGS", -(d.get("cogs") or 0)),
                ("= Gross profit", d.get("gross_profit")),
                ("- Opex (ex-D&A)", -(d.get("sga") or 0)),
                ("= EBITDA", d.get("ebitda")),
                ("- D&A", -(d.get("depreciation_amortization") or 0)),
                ("- Net interest", -((d.get("interest_expense") or 0) - (d.get("interest_income") or 0))),
                ("- Tax", -(d.get("tax_expense") or 0)),
                ("= Net income", d.get("net_income")),
            ]))
        with c2:
            st.markdown("**Asset mix**")
            st.pyplot(charts.donut(
                ["Cash", "ST investments", "Receivables", "Inventory", "Other CA", "PP&E",
                 "Intangibles", "Other NCA"],
                [d.get("cash"), d.get("short_term_investments"), d.get("accounts_receivable"),
                 d.get("inventory"), d.get("other_current_assets"), d.get("net_ppe"),
                 d.get("intangibles"), d.get("other_noncurrent_assets")]))
            st.markdown("**Capital & funding**")
            st.pyplot(charts.donut(
                ["Equity", "ST debt", "LT debt", "Payables", "Other liab."],
                [d.get("total_equity"), d.get("short_term_debt"), d.get("long_term_debt"),
                 d.get("accounts_payable"), d.get("other_liabilities")]))


# --------------------------------------------------------------------------- #
# tab: ratios
# --------------------------------------------------------------------------- #
def tab_ratios(ratios, comp_years):
    years = comp_years
    latest = years[-1]
    key_keys = ["ebitda_margin", "net_debt_ebitda", "ebitda_interest", "ffo_debt",
                "current_ratio", "fcf_debt"]
    if len(years) > 1:
        st.markdown("**Key ratios over time**")
        st.pyplot(charts.multi_line(
            years, {RATIO_BY_KEY[k].label: _series(ratios, years, k) for k in key_keys},
            ylabel="x  /  %"))
        st.divider()

    for cat, defs in by_category().items():
        st.markdown(f"**{cat}**")
        rows = []
        for d in defs:
            rec = {"": STATUS_ICON[d.status(ratios[latest].get(d.key))], "Ratio": d.label}
            for y in years:
                rec[y] = fmt(ratios[y].get(d.key), d.unit, st.session_state.currency)
            rows.append(rec)
        st.dataframe(pd.DataFrame(rows).set_index(""), width="stretch")

        if len(years) > 1:
            scored = [d for d in defs if d.direction in ("higher", "lower")]
            num = pd.DataFrame(
                {y: [ratios[y].get(d.key) for d in scored] for y in years},
                index=[d.label for d in scored]).astype(float)
            txt = pd.DataFrame(
                {y: [fmt(ratios[y].get(d.key), d.unit) for d in scored] for y in years},
                index=[d.label for d in scored])
            if num.notna().any().any():
                st.pyplot(charts.status_heatmap(
                    num, txt, {d.label for d in scored if d.direction == "lower"}))
        st.write("")


# --------------------------------------------------------------------------- #
# tab: credit score
# --------------------------------------------------------------------------- #
def _score_gauge(score: float, rating: str):
    fig, ax = plt.subplots(figsize=(6, 1.5))
    bands = list(reversed(RATING_BANDS))
    edges = [b[0] for b in bands] + [100]
    colors = ["#8b1a1a", "#c0392b", "#e67e22", "#f1c40f", "#a4c93a", "#4a9b3f", "#2e7d32", "#1b5e20"]
    for i, (lo, _r, _k) in enumerate(bands):
        ax.barh(0, edges[i + 1] - lo, left=lo, color=colors[i], height=0.6)
    ax.axvline(score, color="black", lw=3)
    ax.text(score, 0.7, f"{score:.0f}  ({rating})", ha="center", va="bottom",
            fontsize=11, fontweight="bold")
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.5, 1.2)
    ax.set_yticks([])
    ax.set_xticks([0, 20, 40, 60, 64, 80, 100])
    ax.set_frame_on(False)
    fig.tight_layout()
    return fig


def tab_score(ratios, scores, focus, comp_years):
    sc = scores[focus]
    a, b, c, d = st.columns(4)
    a.metric("Composite score", f"{sc.composite:.0f} / 100")
    b.metric("Indicative rating", sc.rating)
    c.metric("Risk category", sc.risk)
    d.metric("Investment grade?", "Yes" if sc.investment_grade else "No")
    st.pyplot(_score_gauge(sc.composite, sc.rating))
    st.caption(f"{focus} · data coverage {sc.coverage:.0%} · confidence {sc.confidence}. "
               "Screening model — see README for methodology and limits.")
    st.divider()

    st.markdown("**Score drivers**")
    rows = []
    for comp in sc.components:
        u = RATIO_BY_KEY[comp.ratio_key].unit
        rows.append({
            "Factor": comp.label,
            "Value": fmt(comp.value, u, st.session_state.currency),
            "Sub-score": None if comp.subscore is None else round(comp.subscore),
            "Weight": f"{comp.raw_weight:.0%}",
            "Contribution": None if comp.subscore is None else round(comp.subscore * comp.weight, 1)})
    cdf = pd.DataFrame(rows)
    st.dataframe(cdf.set_index("Factor"), width="stretch")
    plot = cdf.dropna(subset=["Sub-score"]).set_index("Factor")["Sub-score"]
    if not plot.empty:
        st.bar_chart(plot)

    if len(comp_years) > 1:
        st.divider()
        st.markdown("**Score & rating path**")
        st.pyplot(charts.score_trend(comp_years, [scores[y].composite for y in comp_years], RATING_BANDS))
        path = pd.DataFrame({
            "Score": [round(scores[y].composite) for y in comp_years],
            "Rating": [scores[y].rating for y in comp_years],
            "Risk": [scores[y].risk for y in comp_years],
            "Coverage": [f"{scores[y].coverage:.0%}" for y in comp_years],
        }, index=comp_years)
        st.dataframe(path, width="stretch")
        contrib = {}
        for comp in scores[comp_years[-1]].components:
            contrib[comp.label] = [
                next((cc.subscore * cc.weight for cc in scores[y].components
                      if cc.ratio_key == comp.ratio_key and cc.subscore is not None), 0.0)
                for y in comp_years]
        st.markdown("**Driver contribution to score, by year**")
        st.pyplot(charts.stacked_bars(comp_years, contrib, ylabel="score points"))


# --------------------------------------------------------------------------- #
# tab: stress testing
# --------------------------------------------------------------------------- #
def _heatmap_fig(num: pd.DataFrame, txt: pd.DataFrame, metric: str, marker=None):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    cmap = "RdYlGn_r" if metric_lower_is_better(metric) else "RdYlGn"
    arr = num.to_numpy(dtype=float)
    finite = arr[np.isfinite(arr)]
    vmin, vmax = (float(finite.min()), float(finite.max())) if finite.size else (0, 1)
    if metric == "Composite score":
        vmin, vmax = 0, 100
    im = ax.imshow(arr, cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax)
    ax.set_xticks(range(num.shape[1]), num.columns)
    ax.set_yticks(range(num.shape[0]), num.index)
    ax.set_xlabel("Interest-rate shock (bps)")
    ax.set_ylabel("EBITDA decline")
    for i in range(num.shape[0]):
        for j in range(num.shape[1]):
            ax.text(j, i, txt.iat[i, j], ha="center", va="center", fontsize=9)
    if marker is not None:
        ax.plot(*marker, marker="o", ms=16, mfc="none", mec="black", mew=2)
    fig.colorbar(im, ax=ax, shrink=0.8, label=metric)
    fig.tight_layout()
    return fig


def _scenario_row(res: dict) -> dict:
    return {
        "Score": f"{res['score'].composite:.0f}", "Rating": res["score"].rating,
        "Risk": res["score"].risk,
        "Net debt/EBITDA": fmt(res["ratios"].get("net_debt_ebitda"), "x"),
        "EBITDA/int": fmt(res["ratios"].get("ebitda_interest"), "x"),
        "FCF/debt": fmt(res["ratios"].get("fcf_debt"), "%")}


def tab_stress(plabels, derived, focus):
    sel = st.selectbox("Base year", plabels, index=plabels.index(focus))
    base = derived[sel]

    c1, c2, c3 = st.columns(3)
    rev = c1.slider("Revenue decline (%)", 0, 40, 0, 1)
    ebd = c2.slider("EBITDA decline (%)", 0, 50, 0, 1)
    mgn = c3.slider("Extra EBITDA-margin compression (pp)", 0.0, 10.0, 0.0, 0.5)
    c4, c5, c6 = st.columns(3)
    rate = c4.slider("Interest-rate shock (bps)", 0, 600, 0, 25)
    flt = c5.slider("Floating-rate debt share (%)", 0, 100, 100, 5)
    capx = c6.slider("Capex change (%)", -50, 50, 0, 5)
    inc_debt = st.slider("Incremental debt drawn", 0,
                         int(max(base.get("total_debt") or 0, 100) * 1.5), 0,
                         max(1, int((base.get("total_debt") or 100) / 50)))

    params = StressParams(revenue_decline_pct=rev, ebitda_decline_pct=ebd, ebitda_margin_drop_pp=mgn,
                          rate_shock_bps=rate, floating_debt_pct=flt, capex_change_pct=capx,
                          incremental_debt=inc_debt)
    b = stress_summary(base, StressParams())
    s = stress_summary(base, params)

    st.divider()
    st.markdown(f"**{sel}: base → stressed**")
    m = st.columns(5)
    m[0].metric("Score", f"{s['score'].composite:.0f}",
                f"{s['score'].composite - b['score'].composite:+.0f}")
    m[1].metric("Rating", s["score"].rating,
                "" if s["score"].rating == b["score"].rating else f"was {b['score'].rating}")
    for col, key, unit, lbl in [
        (m[2], "net_debt_ebitda", "x", "Net debt/EBITDA"),
        (m[3], "ebitda_interest", "x", "EBITDA/interest"),
        (m[4], "fcf_debt", "%", "FCF/debt")]:
        bv, sv = b["ratios"].get(key), s["ratios"].get(key)
        delta = "" if bv is None or sv is None else f"{sv - bv:+.2f}"
        col.metric(lbl, fmt(sv, unit), delta,
                   delta_color="inverse" if key == "net_debt_ebitda" else "normal")

    st.divider()
    st.markdown("**Credit-risk heatmap** — EBITDA decline × interest-rate shock")
    metric = st.selectbox("Cell metric", HEATMAP_METRIC_NAMES)
    y_axis, x_axis = [0, 10, 20, 30, 40, 50], [0, 100, 200, 300, 400, 500]
    num, txt = heatmap_frame(base, metric, y_axis, x_axis, floating_debt_pct=flt)
    marker = None
    if ebd <= max(y_axis) and rate <= max(x_axis):
        marker = (np.interp(rate, x_axis, range(len(x_axis))),
                  np.interp(ebd, y_axis, range(len(y_axis))))
    st.pyplot(_heatmap_fig(num, txt, metric, marker))
    st.caption("Circle marks the current slider scenario when it is inside the grid.")

    st.divider()
    st.markdown("**Preset scenarios**")
    rows = [{"Scenario": "Base case", **_scenario_row(b)}]
    for name, p in PRESET_SCENARIOS:
        rows.append({"Scenario": name, **_scenario_row(stress_summary(base, p))})
    rows.append({"Scenario": "Current sliders", **_scenario_row(s)})
    st.dataframe(pd.DataFrame(rows).set_index("Scenario"), width="stretch")


# --------------------------------------------------------------------------- #
# tab: credit memo
# --------------------------------------------------------------------------- #
def _build_ctx(period, derived, ratios, scores, plabels, notes) -> MemoContext:
    hist_years = plabels[-5:]
    history = [{"period": y, "ratios": ratios[y], "score": scores[y]} for y in hist_years]
    return MemoContext(
        company=st.session_state.company or "Unnamed borrower", period=period,
        currency=st.session_state.currency, unit_label=st.session_state.unit_label,
        industry=st.session_state.industry, values=derived[period], ratios=ratios[period],
        score=scores[period], base_params=StressParams(), scenarios=PRESET_SCENARIOS,
        analyst_notes=notes, history=history)


def tab_memo(plabels, derived, ratios, scores, focus):
    sel = st.selectbox("Reporting period for the memo", plabels, index=plabels.index(focus))
    notes = st.text_area("Analyst notes / deal context (optional)", height=110,
                         placeholder="e.g. refinancing $200m of notes maturing FY2026; sponsor-owned; "
                                     "covenant-lite TLB; cyclical end-markets.")
    ctx = _build_ctx(sel, derived, ratios, scores, plabels, notes)

    if not api_key_present():
        st.warning("Memo generation is disabled — set `ANTHROPIC_API_KEY` and restart.")
        with st.expander("Prompt preview (what would be sent to Claude)"):
            from creditlens.memo import build_prompt
            st.code(build_prompt(ctx), language="markdown")
        return

    if st.button("Generate credit review memo", type="primary"):
        sig = hashlib.md5((str(ctx.values) + notes + sel + model_name()
                           + str([s.composite for s in scores.values()])).encode()).hexdigest()
        cache = st.session_state.memo_cache
        if sig not in cache:
            with st.spinner(f"Drafting with {model_name()}…"):
                try:
                    cache[sig] = generate_memo(ctx)
                except MemoConfigError as exc:
                    st.error(str(exc))
                    return
        st.session_state["last_memo"] = cache[sig]

    if st.session_state.get("last_memo"):
        st.divider()
        st.markdown(st.session_state["last_memo"])
        st.download_button("Download memo (.md)", st.session_state["last_memo"],
                           file_name=f"{st.session_state.company or 'borrower'}_credit_memo.md")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    _init_state()
    sidebar_data()
    st.title("Automated Credit Risk Assessment & Scenario Stress-Tester")

    if not st.session_state.mode:
        st.info("👈 Upload annual-report statements, load the sample company, or enter figures manually.")
        st.markdown(
            "- **Upload** raw statements as they appear in the report (granular line items, one or "
            "more year columns, one or more files). CreditLens categorises them itself — cash, "
            "inventories, current portion of debt, long-term loans, leases … roll up into current "
            "assets / current liabilities / total debt automatically, and you can correct any "
            "mapping.\n"
            "- Turn on **Comparison mode** to see 2–5 years of ratios, charts and the credit-score "
            "path side by side.")
        return

    plabels, pdata, derived, ratios, scores, agg = compute()
    if not plabels:
        st.error("No periods to analyse — check the upload on the left.")
        return
    focus, compare, comp_years = sidebar_view(plabels)

    tabs = st.tabs(["📈 Overview", "📄 Statements", "📊 Ratios", "🎯 Credit score",
                    "🌡️ Stress testing", "📝 Credit memo"])
    with tabs[0]:
        tab_overview(plabels, derived, ratios, scores, focus, comp_years)
    with tabs[1]:
        tab_statements(plabels, derived, agg)
    with tabs[2]:
        tab_ratios(ratios, comp_years)
    with tabs[3]:
        tab_score(ratios, scores, focus, comp_years)
    with tabs[4]:
        tab_stress(plabels, derived, focus)
    with tabs[5]:
        tab_memo(plabels, derived, ratios, scores, focus)


if __name__ == "__main__":
    main()
