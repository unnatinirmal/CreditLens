"""Map a set of ratios onto a 0-100 credit score and an indicative rating band.

The model is a transparent weighted average of piecewise-linear sub-scores over
eight credit-relevant ratios. It is a screening aid, not a rating-agency
methodology - the anchor points are illustrative mid-market calibrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

Number = Optional[float]


@dataclass(frozen=True)
class Component:
    ratio_key: str
    label: str
    weight: float
    anchors: list[tuple[float, float]]  # (ratio value, sub-score) - ascending in x

    def subscore(self, value: Number) -> Number:
        if value is None:
            return None
        pts = self.anchors
        if value <= pts[0][0]:
            return pts[0][1]
        if value >= pts[-1][0]:
            return pts[-1][1]
        for (x0, s0), (x1, s1) in zip(pts, pts[1:]):
            if x0 <= value <= x1:
                if x1 == x0:
                    return s1
                t = (value - x0) / (x1 - x0)
                return s0 + t * (s1 - s0)
        return pts[-1][1]


# Anchors read left-to-right in increasing ratio value.
COMPONENTS: list[Component] = [
    Component("net_debt_ebitda", "Net debt / EBITDA", 0.20,
              [(0.5, 100), (1.5, 88), (2.5, 74), (3.5, 58), (4.5, 40), (5.5, 22), (7.0, 5)]),
    Component("ebitda_interest", "EBITDA / Interest", 0.18,
              [(0.5, 0), (1.0, 12), (2.0, 32), (3.0, 48), (4.5, 66), (6.0, 80), (9.0, 94), (12.0, 100)]),
    Component("ffo_debt", "FFO / Total debt (%)", 0.15,
              [(2, 0), (6, 16), (10, 30), (16, 46), (24, 64), (34, 80), (48, 94), (60, 100)]),
    Component("debt_capital", "Debt / (Debt + Equity) (%)", 0.12,
              [(15, 100), (25, 90), (35, 78), (45, 64), (55, 50), (65, 34), (78, 16), (90, 0)]),
    Component("fcf_debt", "FCF / Total debt (%)", 0.12,
              [(-12, 0), (-4, 12), (2, 30), (6, 44), (12, 62), (20, 82), (30, 100)]),
    Component("current_ratio", "Current ratio", 0.08,
              [(0.5, 0), (0.8, 16), (1.0, 34), (1.3, 54), (1.7, 74), (2.2, 92), (2.8, 100)]),
    Component("ebitda_margin", "EBITDA margin (%)", 0.10,
              [(0, 0), (5, 16), (10, 34), (16, 54), (24, 74), (32, 92), (40, 100)]),
    Component("roce", "Return on capital employed (%)", 0.05,
              [(-5, 0), (0, 12), (5, 34), (9, 52), (14, 72), (20, 90), (26, 100)]),
]

TOTAL_WEIGHT = sum(c.weight for c in COMPONENTS)

# (min composite score, rating, risk label). Checked high -> low.
RATING_BANDS: list[tuple[float, str, str]] = [
    (90, "AAA", "Minimal"),
    (82, "AA", "Low"),
    (74, "A", "Low-Moderate"),
    (64, "BBB", "Moderate"),
    (54, "BB", "Elevated"),
    (42, "B", "High"),
    (30, "CCC", "Very High"),
    (0, "CC/C", "Distressed / near default"),
]

INVESTMENT_GRADE_MIN = 64.0


@dataclass
class ComponentResult:
    label: str
    ratio_key: str
    weight: float          # renormalised weight actually applied
    raw_weight: float
    value: Number
    subscore: Number


@dataclass
class CreditScore:
    composite: float
    rating: str
    risk: str
    investment_grade: bool
    coverage: float        # share of raw weight with data (0-1)
    confidence: str
    components: list[ComponentResult]


def rating_for(score: float) -> tuple[str, str]:
    for lo, rating, risk in RATING_BANDS:
        if score >= lo:
            return rating, risk
    return RATING_BANDS[-1][1], RATING_BANDS[-1][2]


def score_credit(ratios: dict[str, Number]) -> CreditScore:
    available = [(c, ratios.get(c.ratio_key)) for c in COMPONENTS]
    covered_weight = sum(c.weight for c, val in available if val is not None)
    coverage = covered_weight / TOTAL_WEIGHT if TOTAL_WEIGHT else 0.0

    results: list[ComponentResult] = []
    weighted_sum = 0.0
    for c, val in available:
        sub = c.subscore(val)
        applied_w = (c.weight / covered_weight) if (covered_weight and sub is not None) else 0.0
        if sub is not None:
            weighted_sum += applied_w * sub
        results.append(ComponentResult(
            label=c.label, ratio_key=c.ratio_key, weight=applied_w,
            raw_weight=c.weight, value=val, subscore=sub))

    composite = round(weighted_sum, 1) if covered_weight else 0.0
    rating, risk = rating_for(composite)

    if coverage >= 0.85:
        confidence = "High"
    elif coverage >= 0.6:
        confidence = "Moderate"
    else:
        confidence = "Low - key inputs missing"

    return CreditScore(
        composite=composite,
        rating=rating,
        risk=risk,
        investment_grade=composite >= INVESTMENT_GRADE_MIN and coverage >= 0.6,
        coverage=coverage,
        confidence=confidence,
        components=results,
    )
