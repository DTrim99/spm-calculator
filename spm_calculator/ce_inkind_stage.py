"""The optional in-kind stage of the CE threshold replication.

BLS adds imputed in-kind benefits "to FCSU out-of-pocket expenditures at
the consumer unit level before the thresholds are estimated"
(https://www.bls.gov/pir/spmhome.htm). This module applies a table of such
amounts, one row per CE ``NEWID`` (for example from
:func:`spm_calculator.ce_inkind.impute_in_kind`), to the frame that
:func:`spm_calculator.ce_threshold.construct_normalized_expenditures`
builds, then estimates thresholds with
:func:`spm_calculator.ce_threshold.estimate_thresholds`.

The stage composes those public functions and changes none of them, so
the out-of-pocket replication, its receipts and the code identities that
seal the canonical forecasts stay as they were.

Every component enters FCSUti. LIHEAP and rental assistance pay for
utilities and shelter, so they also enter shelter and utilities (SU),
which sets the tenure-specific part of each threshold. Amounts are annual
dollars of the unit's reference period; the stage carries them to the
threshold year with the price factor the replication applies to the
unit's out-of-pocket spending. CPI component weights stay those of
out-of-pocket spending.

Invariants, tested in ``tests/test_ce_inkind.py``:

- amounts must be finite and nonnegative, so the stage never lowers any
  unit's FCSUti or SU;
- an all-zero table reproduces the out-of-pocket thresholds and
  normalized expenditures exactly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from spm_calculator.ce_threshold import (
    MEDIAN_SHARE,
    construct_normalized_expenditures,
    estimate_thresholds,
    normalize_ce_sample,
    replicate_thresholds,
)
from spm_calculator.equivalence_scale import REFERENCE_RAW_SCALE

#: In-kind components the stage accepts.
IN_KIND_COMPONENTS = (
    "nslp",
    "wic",
    "liheap",
    "broadband",
    "rental_assistance",
)
#: Components that pay for shelter or utilities and so also enter SU.
IN_KIND_SHELTER_UTILITY_COMPONENTS = ("liheap", "rental_assistance")
#: Prefix of methodology identifiers for runs with the stage switched on.
METHODOLOGY_PREFIX = "ce-fmli-inkind-research-v1"

_OMITTED = "home internet and BLS in-kind benefit imputations omitted"
_REPLACED = "home internet omitted; in-kind benefits from the in-kind stage"
_STAGE_INPUTS = (
    "NEWID",
    "inflation_factor",
    "equiv_scale",
    "fcsuti_threshold_year",
    "su_2a2c",
    "ce_weight",
)


def zero_in_kind(newids) -> pd.DataFrame:
    """An all-zero in-kind table: the stage switched on, nothing imputed."""
    frame = pd.DataFrame({"NEWID": np.asarray(newids)})
    for component in IN_KIND_COMPONENTS:
        frame[component] = 0.0
    frame.attrs["methodology"] = {"implementation": "zero"}
    return frame


def align_in_kind(frame: pd.DataFrame, in_kind: pd.DataFrame) -> pd.DataFrame:
    """Validate an in-kind table and align it to ``frame`` rows by NEWID.

    Every unit of ``frame`` must appear exactly once in ``in_kind``, which
    may hold other units too. Amounts must be finite and nonnegative.
    """
    missing = [c for c in ("NEWID", *IN_KIND_COMPONENTS) if c not in in_kind]
    if missing:
        raise ValueError(f"in_kind is missing columns {missing}")
    if "NEWID" not in frame:
        raise ValueError("The in-kind stage requires CE NEWID")
    table = in_kind[["NEWID", *IN_KIND_COMPONENTS]].copy()
    for column in table.columns:
        try:
            table[column] = pd.to_numeric(table[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise ValueError(f"in_kind {column} must be numeric") from error
    amounts = table[list(IN_KIND_COMPONENTS)].to_numpy(dtype=float)
    if not (np.isfinite(amounts).all() and (amounts >= 0).all()):
        raise ValueError("in_kind amounts must be finite and nonnegative")
    if table["NEWID"].duplicated().any():
        raise ValueError("in_kind NEWID values must be unique")
    keys = pd.to_numeric(frame["NEWID"], errors="raise")
    aligned = pd.DataFrame({"NEWID": keys.to_numpy()}).merge(
        table, on="NEWID", how="left", validate="many_to_one"
    )
    absent = aligned[list(IN_KIND_COMPONENTS)].isna().any(axis=1)
    if absent.any():
        examples = aligned.loc[absent, "NEWID"].head().tolist()
        raise ValueError(f"in_kind lacks sample units, e.g. NEWID {examples}")
    return aligned


def add_in_kind(
    expenditures: pd.DataFrame, in_kind: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    """Add in-kind amounts to normalized CE expenditures.

    Args:
        expenditures: The frame returned by
            :func:`spm_calculator.ce_threshold.construct_normalized_expenditures`.
        in_kind: One row per ``NEWID`` with an annual dollar column for each
            of :data:`IN_KIND_COMPONENTS`.

    Returns:
        A copy of ``expenditures`` whose ``fcsuti_threshold_year``,
        ``fcsuti_2a2c`` and ``su_2a2c`` include the in-kind amounts, with
        ``in_kind_<component>``, ``fcsuti_in_kind`` and ``su_in_kind``
        columns, and the stage's diagnostics.
    """
    missing = [c for c in _STAGE_INPUTS if c not in expenditures]
    if missing:
        raise ValueError(
            "add_in_kind needs construct_normalized_expenditures output; "
            f"missing {missing}"
        )
    aligned = align_in_kind(expenditures, in_kind)
    frame = expenditures.copy()
    for component in IN_KIND_COMPONENTS:
        frame[f"in_kind_{component}"] = aligned[component].to_numpy()
    fcsuti_in_kind = aligned[list(IN_KIND_COMPONENTS)].sum(axis=1).to_numpy()
    su_in_kind = (
        aligned[list(IN_KIND_SHELTER_UTILITY_COMPONENTS)]
        .sum(axis=1)
        .to_numpy()
    )
    frame["fcsuti_in_kind"] = fcsuti_in_kind
    frame["su_in_kind"] = su_in_kind
    factor = frame["inflation_factor"].to_numpy(dtype=float)
    # The same expression construct_normalized_expenditures uses.
    scale = REFERENCE_RAW_SCALE / frame["equiv_scale"]
    frame["fcsuti_threshold_year"] = (
        frame["fcsuti_threshold_year"] + fcsuti_in_kind * factor
    )
    frame["fcsuti_2a2c"] = frame["fcsuti_threshold_year"] * scale
    frame["su_2a2c"] = frame["su_2a2c"] + su_in_kind * factor * scale
    if not np.isfinite(frame[["fcsuti_2a2c", "su_2a2c"]].to_numpy()).all():
        raise ValueError("In-kind expenditures contain nonfinite values")
    weights = frame["ce_weight"].to_numpy(dtype=float)
    stage = {
        "components": list(IN_KIND_COMPONENTS),
        "shelter_utility_components": list(IN_KIND_SHELTER_UTILITY_COMPONENTS),
        "weighted_mean_threshold_year_dollars": {
            component: float(
                np.average(
                    frame[f"in_kind_{component}"].to_numpy() * factor,
                    weights=weights,
                )
            )
            for component in IN_KIND_COMPONENTS
        },
        "weighted_share_with_any": float(
            np.average(fcsuti_in_kind > 0, weights=weights)
        ),
        "methodology": dict(in_kind.attrs.get("methodology", {})),
    }
    return frame, stage


@dataclass(frozen=True)
class OutOfPocketRun:
    """An out-of-pocket replication and the frame the in-kind stage extends.

    Attributes:
        result: :func:`spm_calculator.ce_threshold.replicate_thresholds`
            output, unchanged.
        expenditures: Its normalized expenditures, one row per sample unit.
        median_share: The share of the band mean the thresholds use.
    """

    result: dict
    expenditures: pd.DataFrame
    median_share: float


def prepare_out_of_pocket(
    ce: pd.DataFrame,
    target_year: int,
    *,
    youth_policy: str = "error",
    tenure_policy: str = "exclude_5_6_code3_mortgage",
    mortgage_principal: str = "include",
    annualization: str = "quarter4",
    median_share: float = MEDIAN_SHARE,
    cpi_series: Optional[Mapping[str, pd.Series]] = None,
) -> OutOfPocketRun:
    """Run the out-of-pocket replication and keep its expenditure frame.

    Arguments mirror :func:`spm_calculator.ce_threshold.replicate_thresholds`.
    The frame is rebuilt from the same public stages and must reproduce
    that function's construction and thresholds exactly.
    """
    result = replicate_thresholds(
        ce,
        target_year,
        youth_policy=youth_policy,
        tenure_policy=tenure_policy,
        mortgage_principal=mortgage_principal,
        annualization=annualization,
        median_share=median_share,
        cpi_series=cpi_series,
    )
    normalized, _ = normalize_ce_sample(
        ce, youth_policy=youth_policy, tenure_policy=tenure_policy
    )
    expenditures, construction = construct_normalized_expenditures(
        normalized,
        target_year,
        mortgage_principal=mortgage_principal,
        annualization=annualization,
        cpi_series=cpi_series,
    )
    thresholds, _ = estimate_thresholds(expenditures, median_share)
    if construction != result["construction"] or (
        thresholds != result["thresholds"]
    ):
        raise RuntimeError(
            "Rebuilt out-of-pocket stages differ from replicate_thresholds"
        )
    return OutOfPocketRun(result, expenditures, float(median_share))


def _digest(document: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def replicate_with_in_kind(run: OutOfPocketRun, in_kind: pd.DataFrame) -> dict:
    """Thresholds with in-kind amounts added, in replicate_thresholds' form.

    The methodology configuration is the out-of-pocket one plus an
    ``in_kind`` entry that identifies the imputation and its settings
    (including the implicate). The out-of-pocket thresholds and identifier
    ride along under ``out_of_pocket``.
    """
    frame, stage = add_in_kind(run.expenditures, in_kind)
    thresholds, estimation = estimate_thresholds(frame, run.median_share)
    methodology = stage["methodology"]
    config = {
        **run.result["methodology_config"],
        "in_kind": {
            "implementation": methodology.get("implementation", "unspecified"),
            "components": list(IN_KIND_COMPONENTS),
            "shelter_utility_components": list(
                IN_KIND_SHELTER_UTILITY_COMPONENTS
            ),
            "settings_sha256": _digest(methodology),
        },
    }
    approximations = list(run.result["construction"]["approximations"])
    approximations = [
        _REPLACED if item == _OMITTED else item for item in approximations
    ]
    if _REPLACED not in approximations:
        approximations.append(_REPLACED)
    return {
        **run.result,
        "methodology_id": f"{METHODOLOGY_PREFIX}:{_digest(config)}",
        "methodology_config": config,
        "thresholds": thresholds,
        "construction": {
            **run.result["construction"],
            "approximations": approximations,
            "in_kind": stage,
        },
        "estimation": estimation,
        "out_of_pocket": {
            "methodology_id": run.result["methodology_id"],
            "thresholds": run.result["thresholds"],
        },
        "uncertainty": {
            "status": "unavailable",
            "reason": (
                "Sampling uncertainty not estimated; compare implicates "
                "for imputation uncertainty"
            ),
        },
    }
