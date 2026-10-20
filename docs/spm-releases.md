# Forecast artifacts and archived releases

Version 1.1.0 uses the canonical schema-2 forecast artifact for standalone
calculations and current model adapters. Its default coverage is 2022–2036,
with `ce_trend` and `zero_real` scenarios. Published 2025 national thresholds
and BLS shelter/utilities shares remain exact inputs; modeled local rent
indices and future values carry their own statuses.

The PolicyEngine, Microcosm and Axiom adapters ship inside this package; the
PolicyEngine country and wrapper integration ships in `policyengine-us` 2.0
and the `policyengine` wrapper 6.0, both in progress, and the
[1.0 migration guide](migration.md) describes the coordinated pins. In that
integration the country model reads forecast configuration by default. It does
not require an opt-in historical release path or support year/geography
fallback flags.

## Schema-2 forecast contract

The default file is
[`rolling_forecast_2026_09_09.json`](../spm_calculator/data/current/rolling_forecast_2026_09_09.json).
`SPMForecast.from_dict` and `load_forecast` validate it. The archived
`data/releases/schema-v1.json` describes schema-1 releases, not this schema-2
forecast; the forecast's current validator is
[`rolling_forecast.py`](../spm_calculator/rolling_forecast.py).

| Field or path | Meaning |
| --- | --- |
| `schema_version` | Integer `2`. |
| `method` | `rolling_ce_acs_v1`. |
| `units` | `USD/year`. |
| `reference_family` | `{"adults": 2, "children": 2}`. |
| `forecast_id`, `content_sha256` | Artifact identity and canonical content digest. |
| `created_on`, `information_date` | Build and retained-information dates. |
| `base_release_sha256` | Identity of the archived published input release used by assembly. |
| `sources` | List of source IDs, URLs, hashes and availability metadata. |
| `assumptions`, `assumption_sha256` | Declared assumptions and their digest. |
| `component_sha256`, `code_sha256` | Component and implementation identities. |
| `default_scenario` | Currently `ce_trend`. |
| `scenarios.<scenario>.years.<year>` | Selected-year values, statuses and window diagnostics; JSON year keys are strings. |
| `areas.<area_id>` | Area name and actual area type. |
| `county_assignments` | County vintage, boundary metadata, mapping digest, `year_maps` and `maps`. |
| `validation`, `forward_test`, `rent_sensitivity` | Evaluation and sensitivity records with their stated scope. |

Inspect these actual field names rather than constructing an incomplete
artifact by hand:

```python
from spm_calculator import load_forecast

forecast = load_forecast()
document = forecast.to_dict()
assert document["schema_version"] == 2
assert document["units"] == "USD/year"
assert document["reference_family"] == {"adults": 2, "children": 2}
entry = document["scenarios"]["ce_trend"]["years"]["2025"]
print(entry["national_status"], entry["housing_share_status"])
print(entry["thresholds"]["renter"], entry["housing_shares"]["renter"])
assignment = forecast.resolve_county(2025, "06037", county_vintage="2020")
print(entry["geography_by_area"][assignment["area_id"]])
```

Each year entry contains `thresholds`, `housing_shares`, `rent_indices`,
`national_status`, `housing_share_status`, `geography_status`,
`geography_by_area`, `median_diagnostics`, `ce_window` and `acs_window`, plus
national and housing-share source IDs. `thresholds` and `housing_shares` are
keyed by the three named tenures. `rent_indices`, `geography_by_area` and
`median_diagnostics` are keyed by area ID.

The area metadata's `official_published_area` flag identifies its official
anchor status, not whether every target-year amount is published. Inspect
`status`, `anchor_status`, source IDs and support diagnostics. An entry-wide
`geography_status="mixed"` must not be shown as the selected area's status.
County mapping records describe assignments separately; county is never the
estimation unit. See [geography](api/geography.md).

## Integrity and information dates

The content digest is SHA-256 of UTF-8 JSON with sorted keys, compact separators,
unescaped non-ASCII text and no nonfinite numbers, excluding `content_sha256`
itself. File-byte hashes differ because indentation and the embedded digest
change the serialized bytes. Retain a reviewed content digest independently
and pass it to `load_forecast(expected_sha256=...)` on replay.

Accessors return detached values; the verified forecast remains immutable.
An `as_of` earlier than `information_date` fails. The retained-source dates do
not reconstruct a historical real-time information set. Hashes establish
integrity against an expectation, not source authenticity or signed admission.
A provenance-only source refresh changes the artifact and bound runtime bundle
identities even if all amounts remain equal.

## Current artifact identity

Version 1.1.0 uses these pins:

| Identity | SHA-256 |
| --- | --- |
| Canonical content (`expected_sha256` / `forecast_content_sha256`) | `335e6755dc8e0bd883e09b76c2bd53a5315683773e44d50085c7d6b8832b6291` |
| Canonical file bytes | `53d2b4b32251d3afbcf62a76c0f843c2a9369b3565acf088d6729b3bd8069d1b` |
| Assumptions | `6ef10c97d39a95720cce7993036b3db702b9f7c8c71a1e1ec37c104d85f18dfd` |

The [current canonical download](../web/public/data/canonical/rolling-forecast-53d2b4b32251d3afbcf62a76c0f843c2a9369b3565acf088d6729b3bd8069d1b.json)
has the file-byte digest above. Each downstream bundle declares its own
coordinated pins.

Versions 1.0.0 through 1.0.1 cover 2022–2035 with content
`3d86d5c4c0423480e6b69b75d222ffa4a7a2639e4094df5ba2504af01be17173`, file bytes
`cc06784feb81f8c7935d4494cea0a9821a79af868ac50383da8be37c6dc14b99` and
assumptions `d81cf3b1a131fb386941e2e5c30792545f7dc965f6ba4c4dfb13e8ca95a3992d`.
That artifact remains available as an
[immutable download](../web/public/data/canonical/rolling-forecast-cc06784feb81f8c7935d4494cea0a9821a79af868ac50383da8be37c6dc14b99.json).

Version 1.1.0 extends the horizon to 2036. The CE and ACS components are
rebuilt from cached raw inputs and the forecast is reassembled. The
[horizon receipt](../spm_calculator/data/current/scientific_refresh_receipt.json)
records exact comparisons against the 1.0.x artifact. Every 2022–2035 field
and all 2,110,752 retained calculations are unchanged. Only the 2036 entries,
the extended code identities, the rebuilt components and the seals differ.
The receipt also runs 150,768 calculations for 2036. The
[extension archive](../spm_calculator/data/provenance/horizon-2036-extension/README.md)
keeps the replaced component bytes and the previous receipt.

The September 9, 2026 Python AST portability adaptation changed source and
artifact identities while preserving all scientific values. The
[adaptation receipt](../spm_calculator/data/current/acs_code_identity_adaptation.json)
links the original and adapted source bytes, unchanged normalization-function
bytes and cache identities, and the
[retained original evidence](../spm_calculator/data/provenance/ast-portability-2026-09-09/README.md).
The earlier normalization receipt still records its original 616,858-record
reparse. The 1.1.0 rebuild replaces the adapted ACS component. Tests now read
the adapted bytes from the extension archive, and `adapt_acs_code_identity.py`
no longer reproduces current files. Its
[original immutable download](../web/public/data/canonical/rolling-forecast-76ab8435f087f167ad01b8495ebd016415ba8086f32bfbd3dab961dcc8976c0a.json)
remains available. Historical normalization, scientific-refresh and
acceptance evidence retain their original identities and meaning.
Downstream bundles must explicitly adopt the new content digest.

The active browser inputs are
[`web/public/data/release_config.json`](../web/public/data/release_config.json).
Their `forecast.auditArtifact` identifies the current immutable canonical
download by file SHA-256. Historical content-addressed downloads remain
available; obsolete `/data/current/` routes are absent.

Verify the assembly without raw microdata, and repeat the horizon comparison:

```sh
python scripts/build_rolling_forecast.py --check
python scripts/verify_horizon_extension.py --output spm_calculator/data/current/scientific_refresh_receipt.json
git diff --exit-code spm_calculator/data/current/scientific_refresh_receipt.json
```

`--no-calculate` skips the calculation grid and prints the field comparison.

The committed browser export carries the 1.1.0 scientific inputs but keeps
the verified PyPI package pin 1.0.0.post1 until 1.1.0 is promoted.
`tests/test_web_config.py` checks that only the package fields differ from a
fresh export. Once 1.1.0 is on PyPI, promote and verify the export with:

```sh
python scripts/export_web_release.py --published-package-version 1.1.0
python scripts/export_web_release.py --published-package-version 1.1.0 --check
```

The exporter requires the publication flag to match `pyproject.toml`. Omit the
flag when generating and checking an unpublished preview export, whose default
status is `local_preview`. Package publication and browser promotion happen
separately; the live app retains its verified package pin until promotion.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| SPM calculator | Published lookup, CE/ACS estimation, assumptions, artifact validation, equivalence and measurement amounts. |
| PolicyEngine | Native membership and measurement roles, tax/benefit/resource formulas, simulation-specific forecast configuration and storage casts. |
| Microcosm Frame | Declared entity links, typed weights, immutable provenance and native population accounting. |
| Axiom core | Native primitive classification and related unit counts, dated parameter/scale lookups, threshold/housing/poverty arithmetic and execution receipts. |

The Axiom adapter exports a bounded canonical equivalence table because the
runtime lacks fractional power. It does not receive final thresholds or
geographic/equivalence factors as per-unit facts. See
[actual Axiom execution and numeric limits](axiom-integration.md). None of these
adapters supplies population certification or turns conditional forecasts into
published statistics.

## Archived inputs and experiments

The sealed schema-1 file
[`spm-2026-09-08.json`](../spm_calculator/data/releases/spm-2026-09-08.json)
and `SPMRelease` reader remain for archived input replay. That artifact contains
its original carried-share/geography assumptions and content identity. It is
not the current CLI, country/provider, Frame or Axiom runtime selection path.
Preserving it does not reinstate removed calculator or projection APIs.

The [2026 correction record](bls-2026-correction.md) preserves publication
vintages, historical replication results and the frozen 2025 forecast.
The [September 8 CE experiment](current-ce-replication.md) records a separate
retrospective exercise. Their committed amounts and commitments are not
rewritten by the current rolling forecast or documentation updates.

## Replay and verify

```sh
python -m spm_calculator.cli verify
python -m spm_calculator.cli calculate --year 2025 --adults 2 --children 2 --tenure renter --national
python -m spm_calculator.cli --scenario zero_real export --format csv
```

`--forecast`, `--expect-sha256`, `--as-of` and `--scenario` are global options
placed before the subcommand. The [quickstart](quickstart.md) demonstrates a
file/digest round trip. [Validation](validation.md) separates artifact checks
from scientific source verification and optional real-runtime acceptance.
