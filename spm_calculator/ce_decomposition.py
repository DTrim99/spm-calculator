"""Exact decomposition of SPM threshold growth.

Write ``F(d, k, p)`` for the threshold estimated from the CE window of
target year ``d``, with the in-kind stage off (``k = 0``) or on
(``k = 1``), in prices of year ``p``. Growth from target year ``a`` to
``b`` of the full (in-kind) replication factors exactly as::

    F(b,1,b) / F(a,1,a) = price x real x in_kind

    price   = F(a,0,b) / F(a,0,a)        (package composite CPI)
            or I_b / I_a                 (BLS FCSUti index)
    real    = [F(b,0,b) / F(a,0,a)] / price
    in_kind = [F(b,1,b) / F(b,0,b)] / [F(a,1,a) / F(a,0,a)]

``real`` is the growth of out-of-pocket FCSUti beyond prices: new CE
data, sample composition and real spending. ``in_kind`` is the change in
the proportional lift that imputed benefits give the threshold. The
package's composite CPI rebases its weights to each window, so its price
term needs the counterfactual run ``F(a,0,b)``; BLS's index makes the
price term a scalar. Additive percentage-point contributions use the
sequential split ``(price - 1) + price (real - 1) + price real
(in_kind - 1)``, which sums to total growth exactly.
"""

from __future__ import annotations

import math
from typing import Mapping

TENURES = ("renter", "owner_with_mortgage", "owner_without_mortgage")

#: BLS FCSUti index, annual averages, 2013 = 100, from BLS's table
#: "Year-over-Year Inflation Rate: All Items CPI-U, All Items C-CPI-U and
#: FCSUti: 2002-2025 (2013=100)" at
#: https://www.bls.gov/pir/spm/spm_chart_2025data.htm (page last modified
#: September 24, 2026; read November 5, 2026). BLS imputes October 2025,
#: which it did not collect, by geometric interpolation between the
#: September and November item-area indexes
#: (https://www.bls.gov/pir/spm/spm_thresholds_2025.htm).
BLS_FCSUTI_SOURCE = "https://www.bls.gov/pir/spm/spm_chart_2025data.htm"
BLS_FCSUTI_INDEX = {
    2002: 77.980,
    2003: 79.757,
    2004: 81.695,
    2005: 83.964,
    2006: 86.716,
    2007: 89.442,
    2008: 92.876,
    2009: 93.967,
    2010: 94.312,
    2011: 96.168,
    2012: 98.093,
    2013: 100.000,
    2014: 102.465,
    2015: 104.269,
    2016: 106.180,
    2017: 108.378,
    2018: 110.888,
    2019: 113.452,
    2020: 116.160,
    2021: 119.842,
    2022: 128.478,
    2023: 136.408,
    2024: 141.831,
    2025: 146.712,
}


def bls_fcsuti_index(year: int) -> float:
    """BLS's published FCSUti index level for ``year`` (2013 = 100)."""
    try:
        return BLS_FCSUTI_INDEX[int(year)]
    except KeyError:
        raise ValueError(f"No BLS FCSUti index for {year}") from None


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def decompose_growth(
    *,
    base_without: Mapping[str, float],
    base_with: Mapping[str, float],
    target_without: Mapping[str, float],
    target_with: Mapping[str, float],
    price: Mapping[str, float] | float,
) -> dict:
    """Factor threshold growth into price, real spending and in-kind.

    Args:
        base_without: ``F(a,0,a)`` by tenure.
        base_with: ``F(a,1,a)`` by tenure.
        target_without: ``F(b,0,b)`` by tenure.
        target_with: ``F(b,1,b)`` by tenure.
        price: Price factor by tenure (``F(a,0,b)/F(a,0,a)``) or one
            scalar (``I_b/I_a``).

    Returns:
        Per tenure: the multiplicative factors, their log points, the
        additive percentage-point contributions, and the identity residual
        (``total - price * real * in_kind``), which is zero up to rounding.
    """
    out = {}
    for tenure in TENURES:
        a0 = _positive(base_without[tenure], "base_without")
        a1 = _positive(base_with[tenure], "base_with")
        b0 = _positive(target_without[tenure], "target_without")
        b1 = _positive(target_with[tenure], "target_with")
        pi = _positive(
            price[tenure] if isinstance(price, Mapping) else price, "price"
        )
        total = b1 / a1
        real = (b0 / a0) / pi
        in_kind = (b1 / b0) / (a1 / a0)
        out[tenure] = {
            "factors": {
                "total": total,
                "price": pi,
                "real_out_of_pocket": real,
                "in_kind": in_kind,
                "out_of_pocket_total": b0 / a0,
            },
            "log_points": {
                "total": 100 * math.log(total),
                "price": 100 * math.log(pi),
                "real_out_of_pocket": 100 * math.log(real),
                "in_kind": 100 * math.log(in_kind),
            },
            "percentage_points": {
                "total": 100 * (total - 1),
                "price": 100 * (pi - 1),
                "real_out_of_pocket": 100 * pi * (real - 1),
                "in_kind": 100 * pi * real * (in_kind - 1),
            },
            "identity_residual": total - pi * real * in_kind,
        }
    return out


def growth_percent(
    base: Mapping[str, float], target: Mapping[str, float]
) -> dict[str, float]:
    """Percent change by tenure."""
    return {
        tenure: 100
        * (
            _positive(target[tenure], "target")
            / _positive(base[tenure], "base")
            - 1
        )
        for tenure in TENURES
    }
