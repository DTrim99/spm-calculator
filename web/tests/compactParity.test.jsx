import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";

import { expandCalculatorData } from "../lib/loadCalculatorData";
import { calculateGeoadj } from "../lib/geoadj";
import {
  getForecastValidation,
  getHistoricalSeriesBreaks,
  getMedianDiagnostic,
} from "../lib/forecastValidation";
import { getRawEquivalenceScale } from "../src/components/CalculatorWorkbench";

const YEARS = Array.from({ length: 15 }, (_, offset) => 2022 + offset);
const TENURES = ["renter", "owner_with_mortgage", "owner_without_mortgage"];
// Independent raw Betson reference values cover every branch of the UI helper.
const HOUSEHOLDS = [
  [1, 0, 1],
  [1, 1, 1.8 ** 0.7],
  [1, 3, 2.8 ** 0.7],
  [2, 0, 1.41],
  [2, 2, 3 ** 0.7],
  [3, 0, 3 ** 0.7],
  [4, 3, 5.5 ** 0.7],
];

function medianPresentation(diagnostic) {
  if (!diagnostic) return null;
  return Object.fromEntries(
    [
      "thinSupport",
      "materialTopcoding",
      "hasTopcoding",
      "indexTopcoding",
      "topcodedShare",
      "unique_records",
      "kish_effective_count",
    ].map((key) => [key, diagnostic[key]]),
  );
}

function validationPresentation(validation) {
  return Object.fromEntries(
    Object.entries(validation).map(([component, evaluation]) => [
      component,
      Object.fromEntries(
        [
          "used",
          "complete",
          "researchComplete",
          "underperformed",
          "unsupportedArea",
          "unsupportedHorizon",
          "supportedHorizons",
          "horizon",
          "testedHorizon",
        ].map((key) => [key, evaluation[key]]),
      ),
    ]),
  );
}

describe("compact browser export parity with the scientific artifact", () => {
  let canonical;
  let wire;
  let data;
  let canonicalForecast;

  beforeAll(() => {
    canonical = JSON.parse(
      readFileSync(
        "../spm_calculator/data/current/rolling_forecast_2026_09_09.json",
        "utf8",
      ),
    );
    wire = JSON.parse(readFileSync("public/data/release_config.json", "utf8"));
    data = expandCalculatorData(wire);
    canonicalForecast = {
      baseYear: canonical.national_anchor_year,
      geographyAnchorYear: canonical.assumptions.acs.anchor_spm_year,
      scenarios: canonical.scenarios,
      validation: canonical.validation,
    };
  });

  it("pins the full scientific artifact and preserves all 12 published source cells exactly", () => {
    expect(wire.uiSchemaVersion).toBe(1);
    expect(data.forecast.contentSha256).toBe(canonical.content_sha256);
    expect(data.forecast.assumptionSha256).toBe(canonical.assumption_sha256);
    expect(data.forecast.baseReleaseSha256).toBe(canonical.base_release_sha256);
    expect(data.forecast.componentSha256).toEqual(canonical.component_sha256);
    expect(data.forecast.codeSha256).toEqual(canonical.code_sha256);
    expect(data.forecast.defaultScenario).toBe(canonical.default_scenario);
    expect(Object.keys(data.forecast.scenarios)).toEqual(
      Object.keys(canonical.scenarios),
    );
    for (const [scenarioId, scenario] of Object.entries(canonical.scenarios)) {
      let sourceCells = 0;
      for (const year of [2022, 2023, 2024, 2025]) {
        const actual = data.forecast.scenarios[scenarioId].years[year];
        const expected = scenario.years[year];
        expect(actual.national_status).toBe("published");
        expect(actual.housing_share_status).toBe("published_anchor");
        for (const tenure of TENURES) {
          expect(actual.thresholds[tenure]).toBe(expected.thresholds[tenure]);
          expect(actual.housing_shares[tenure]).toBe(
            expected.housing_shares[tenure],
          );
          expect(actual.national_source_ids).toEqual(
            expected.national_source_ids,
          );
          expect(actual.housing_share_source_ids).toEqual(
            expected.housing_share_source_ids,
          );
          sourceCells += 1;
        }
      }
      expect(sourceCells).toBe(12);
    }
  });

  it.each(YEARS)(
    "replays %i across 2 scenarios × 349 actual areas × 3 tenures × seven household compositions",
    (year) => {
      let combinations = 0;
      expect(data.methodology.equivalenceScale.referenceFamilyRaw).toBe(3 ** 0.7);
      for (const [adults, children, raw] of HOUSEHOLDS) {
        expect(getRawEquivalenceScale(adults, children, data.methodology)).toBe(
          raw,
        );
      }
      expect(Object.keys(canonical.scenarios)).toHaveLength(2);
      for (const [scenarioId, scenario] of Object.entries(canonical.scenarios)) {
        expect(Object.keys(scenario.years).map(Number)).toEqual(YEARS);
        const expected = scenario.years[year];
        const actual = data.forecast.scenarios[scenarioId].years[year];
        const areas = Object.keys(expected.geography_by_area).sort();
        expect(areas).toHaveLength(349);
        expect(Object.keys(actual.geography_by_area).sort()).toEqual(areas);
        expect(Object.keys(data.areasByYear[year]).sort()).toEqual(areas);
        expect(Object.keys(actual.rent_indices).sort()).toEqual(areas);
        for (const areaId of areas) {
          expect(data.areasByYear[year][areaId].name).toBe(
            canonical.areas[areaId].name,
          );
          expect(data.areasByYear[year][areaId].area_type).toBe(
            canonical.areas[areaId].area_type,
          );
          expect(actual.rent_indices[areaId]).toBe(
            expected.rent_indices[areaId],
          );
          for (const tenure of TENURES) {
            expect(actual.thresholds[tenure]).toBe(expected.thresholds[tenure]);
            expect(actual.housing_shares[tenure]).toBe(
              expected.housing_shares[tenure],
            );
            const factor = calculateGeoadj({
              rentIndex: actual.rent_indices[areaId],
              housingShare: actual.housing_shares[tenure],
            });
            const expectedFactor =
              1 +
              expected.housing_shares[tenure] *
                (expected.rent_indices[areaId] - 1);
            for (const [adults, children, raw] of HOUSEHOLDS) {
              const actualThreshold =
                actual.thresholds[tenure] *
                (getRawEquivalenceScale(adults, children, data.methodology) /
                  data.methodology.equivalenceScale.referenceFamilyRaw) *
                factor;
              const expectedThreshold =
                expected.thresholds[tenure] * (raw / 3 ** 0.7) * expectedFactor;
              expect(
                actualThreshold,
                `${scenarioId}/${year}/${areaId}/${tenure}/${adults}A${children}C`,
              ).toBeCloseTo(expectedThreshold, 8);
              combinations += 1;
            }
          }
        }
      }
      expect(combinations).toBe(2 * 349 * 3 * HOUSEHOLDS.length);
    },
  );

  it("preserves warning decisions and displayed diagnostic numbers for every actual area and year", () => {
    let thinSupportCount = 0;
    let topcodingCount = 0;
    for (const [scenarioId, scenario] of Object.entries(canonical.scenarios)) {
      for (const year of YEARS) {
        const expected = scenario.years[year];
        const actual = data.forecast.scenarios[scenarioId].years[year];
        for (const areaId of Object.keys(expected.geography_by_area)) {
          const context = `${scenarioId}/${year}/${areaId}`;
          const canonicalMedian = getMedianDiagnostic(expected, areaId);
          expect(
            medianPresentation(getMedianDiagnostic(actual, areaId)),
            context,
          ).toEqual(medianPresentation(canonicalMedian));
          thinSupportCount += Number(canonicalMedian?.thinSupport === true);
          topcodingCount += Number(canonicalMedian?.hasTopcoding === true);
          expect(
            validationPresentation(
              getForecastValidation(
                data.forecast,
                scenarioId,
                year,
                actual,
                areaId,
              ),
            ),
            context,
          ).toEqual(
            validationPresentation(
              getForecastValidation(
                canonicalForecast,
                scenarioId,
                year,
                expected,
                areaId,
              ),
            ),
          );
          expect(
            getHistoricalSeriesBreaks(
              data.forecast,
              scenarioId,
              areaId,
              actual,
            ),
            context,
          ).toEqual(
            getHistoricalSeriesBreaks(
              canonicalForecast,
              scenarioId,
              areaId,
              expected,
            ),
          );
        }
      }
    }
    expect(thinSupportCount).toBeGreaterThan(0);
    expect(topcodingCount).toBeGreaterThan(0);
  });
});
