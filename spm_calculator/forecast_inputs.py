"""Pinned historical components and annual-average future price assumptions."""

import hashlib
import json
from pathlib import Path

from spm_calculator.release import canonical_bytes


def load_horizon_inputs():
    """Read compact source inputs without network access or implicit tail rates."""
    path = Path(__file__).parent / "data/current/forecast_horizon_inputs.json"
    document = json.loads(path.read_text())
    actual = hashlib.sha256(
        canonical_bytes(
            {k: v for k, v in document.items() if k != "content_sha256"}
        )
    ).hexdigest()
    if actual != document.get("content_sha256"):
        raise ValueError("Horizon source input hash mismatch")
    return document


def projection_rates(*, end_year=2036):
    rates = load_horizon_inputs()["prices"]["annual_growth_by_year"]
    expected = set(range(2026, end_year + 1))
    if end_year < 2026 or expected - {int(y) for y in rates}:
        raise ValueError("Price projections have incomplete year coverage")
    return {year: rates[str(year)] for year in sorted(expected)}
