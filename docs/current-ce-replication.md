# September 8, 2026 CE replication experiment

This experiment was run on September 8, 2026, from the cached CE Interview
PUMD files. It is a retrospective experiment with the corrected published
threshold series. It does not alter the original 2025 forecast commitment
or establish a real-time forecast evaluation.

The immutable result is
[ce_replication_2019_2025.json](../spm_calculator/data/current/ce_replication_2019_2025.json).
It includes the archived projection evaluation and identities of code that
has since been removed. The current script replays the source-replication
stages into a separate output; it does not regenerate the historical
projection evaluation or overwrite this receipt.

To inspect the current command without loading CE data:

```sh
python scripts/replicate_current_ce.py --help
```

A deliberate cached-source replay can use:

```sh
python scripts/replicate_current_ce.py --output benchmark_output/ce_source_replication_2019_2025.json
```

The script requires the cached CE ZIPs in
`~/.cache/spm-calculator/ce-pumd` (or `--cache-dir`) and the explicitly
selected annual CPI file (`--cpi-input`). It refuses to download missing
CE inputs and does not call the CPI API. The current replay records SHA-256 hashes
for all 11 ZIPs, 44 selected quarter members, the CPI file and the source
code. Original CE publication and revision dates are unknown; the manifest
records that these exact bytes were available at the replay date.

## Scientific stages

The current functions live in `spm_calculator.ce_threshold`.
`normalize_ce_sample` validates the reported counts and survey weights,
selects the child-CU sample, and records every exclusion reason, count and
weight. `construct_normalized_expenditures` constructs expenditures,
derives CPI shares from that same sample, applies explicit annual CPI
inputs, and normalizes to the reference family. `estimate_thresholds`
uses a stable positional selection of the inclusive 47–53 midpoint-CDF
band and records its observations, weights, component averages and
tenure-specific averages. `replicate_thresholds` combines the stages and
returns their diagnostics. The original `calculate_base_thresholds`
entry point returns a threshold dictionary. These are research tools,
separate from the canonical `load_forecast().calculate_unit(...)` consumer.

The percentile convention is explicit but has not been matched to BLS's
unpublished percentile implementation. Degenerate small samples use the
existing nearest-median/pooled-tenure fallback and report it. None of the
full 2019–2025 baseline runs required either fallback. Duplicate source
indexes produce identical thresholds; a check using the real 2024 window
is recorded in the artifact.

## Minor-only consumer units

The [CE consumer-unit definition](https://www.bls.gov/cex/csxgloss.htm)
permits financially independent minors. Consequently, a unit with
`FAM_SIZE == PERSLT18` is not necessarily malformed. For example, the
2020Q4 data contain NEWID 4429352, with a 16-year-old reference person,
one person, one person under 18, tenure 2, and weight 39,790.985.

The [BLS SPM methodology](https://www.bls.gov/pir/spmhome.htm) describes
adult-containing equivalence formulas, but an exact treatment of these
minor-only CE units could not be established. The default `youth_policy`
therefore raises an error. This experiment explicitly selects
`exclude_unresolved`, an approximation. Original counts remain unchanged.
`legacy_recode` is a separately labeled diagnostic imposing one adult
while retaining the reported child count. It is a nonofficial sensitivity
assumption, not a proposed reconstruction of membership or dependency.

The seven target windows contained 12, 12, 7, 5, 1, 1 and 1 unresolved
interviews, respectively. They represent 0.005–0.033% of the child-CU
survey-weight sum. Switching from exclusion to the diagnostic recode
changed thresholds by at most **0.0411%**. These are overlapping windows
of interviews; their counts and weight sums must not be added up as
unique people or unique national consumer units.

This sensitivity does not identify the correct BLS policy. In particular,
it does not resolve classification of independent teenagers in larger
units. A future exact implementation requires documented BLS treatment
and any member/dependency data necessary to apply it.

## Tenure sensitivity and replication levels

The [CE dictionary](https://www.bls.gov/cex/pumd/ce-pumd-interview-diary-dictionary.xlsx)
defines tenure 3 as owned with mortgage status unreported, 5 as occupied
without cash rent, and 6 as student housing. The baseline maps 3 to
ownership with mortgage and excludes 5/6 before CPI-weight construction
and percentile selection. These are explicit research policies; their
equivalence to BLS's exact sample selection remains unverified.

Including 5/6 as renters changed a threshold by as much as **1.7372%**.
Their excluded weight share was 1.42–1.56% of the child-CU sample. No
tenure-3 records appeared in the selected windows, so reassigning that
code had zero measured effect and provides no evidence about its effect
in data where it is present.

Signed percentage differences from the corrected published thresholds:

| Target | Renter | Owner with mortgage | Owner without mortgage |
|---|---:|---:|---:|
| 2019 | −1.12% | −1.67% | −1.00% |
| 2020 | −1.45% | −1.69% | −0.24% |
| 2021 | −1.36% | −1.55% | +0.78% |
| 2022 | −1.33% | −1.41% | +1.67% |
| 2023 | −1.07% | −2.05% | +0.72% |
| 2024 | −1.40% | −2.25% | +1.14% |
| 2025 | −2.77% | −1.79% | +0.11% |

Annual CPI rather than quarterly treatment, the food-summary allocation,
omitted internet expenditures, omitted BLS in-kind benefit imputations,
and the percentile convention remain separately unmeasured approximations.
The youth result does not establish that these other approximations are
negligible. No sampling or imputation uncertainty interval is estimated.

## Archived projection evaluation

The September 8 experiment used the former `projection.ProjectionInput`
and `project_thresholds` interfaces. Those APIs have been removed; their
names here identify the archived experiment, not executable current
instructions. The former helper multiplied an official base by a
replication ratio, price-index ratio or declared blend and checked dated
input compatibility. The immutable receipt preserves that experiment's
inputs and code identities.

For that 2020–2025 retrospective exercise, mean absolute percentage
errors across the three tenures and six target years are:

| Method | Mean absolute percentage error |
|---|---:|
| Replication growth ratio | 0.5061% |
| 50/50 replication and composite-price blend | 0.8995% |
| Composite-price ratio | 1.6499% |
| All-items CPI-U ratio | 2.2868% |

The inputs are conservatively dated at this replay, rather than assigned
invented historical availability dates. Composite-price inputs use the
static shares rebased to each previous year, differing from the fixed
2019 price base in the frozen historical experiment. These comparisons
describe the September 8 method and must not replace earlier commitments
or be described as prospective evidence. This archived experiment made
no new 2026 forecast. The separate [canonical rolling forecast](rolling-forecasts.md)
now advances CE and ACS windows through 2036 under explicit conditional
assumptions and carries its own evaluation.

Replication results carry a stable configuration fingerprint covering sample
policies, mortgage-principal treatment, annualization, the median share,
percentile convention, and CPI input mode. The archived projection inputs
used each result's actual fingerprint to reject mixed methods. The immutable receipt retains the published
threshold series and its then-current loader identity, alongside CE and
CPI source identities. Current rolling components carry separate code
and input hashes.
