import {
  renderCalculator as render,
  selectArea,
} from "./helpers/renderCalculator";
import {
  fireEvent,
  render as renderSetup,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";

import { expandCalculatorData } from "../lib/loadCalculatorData";
import CalculatorWorkbench from "../src/components/CalculatorWorkbench";

const YEARS = Array.from({ length: 15 }, (_, offset) => 2022 + offset);
const readCalculatorData = () =>
  expandCalculatorData(
    JSON.parse(readFileSync("public/data/release_config.json", "utf8")),
  );
const readCanonicalArtifact = () =>
  JSON.parse(
    readFileSync(
      "../spm_calculator/data/current/rolling_forecast_2026_09_09.json",
      "utf8",
    ),
  );
const selectYear = (year) =>
  fireEvent.change(screen.getByLabelText("Threshold year"), {
    target: { value: String(year) },
  });

afterEach(() => vi.unstubAllGlobals());

describe("canonical export integration", () => {
  it("discovers the actual historical area before choosing a year and rejects an incompatible year", () => {
    const fetch = vi.fn(() => {
      throw new Error("Network unavailable");
    });
    vi.stubGlobal("fetch", fetch);
    renderSetup(<CalculatorWorkbench data={readCalculatorData()} />);
    const search = screen.getByLabelText("Search SPM areas");
    fireEvent.change(search, { target: { value: "45001" } });
    fireEvent.click(
      within(
        screen.getByRole("listbox", { name: "Matching SPM areas" }),
      ).getByRole("option", { name: "South Carolina Metro", exact: true }),
    );
    fireEvent.change(screen.getByLabelText("Adults"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByLabelText("Children"), {
      target: { value: "0" },
    });
    fireEvent.mouseDown(
      screen.getByRole("tab", { name: "Renter", exact: true }),
      { button: 0, ctrlKey: false },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Continue", exact: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: "2023", exact: true }));
    expect(
      screen.getByRole("heading", { name: "Where do you live?" }),
    ).toBeVisible();
    expect(screen.queryByTestId("primary-result")).toBeNull();
    fireEvent.change(screen.getByLabelText("Search SPM areas"), {
      target: { value: "45001" },
    });
    expect(
      screen.queryByRole("option", {
        name: "South Carolina Metro",
        exact: true,
      }),
    ).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("loads schema 2 with no obsolete release, nowcast or global-union menu inputs", async () => {
    const data = readCalculatorData();
    expect(data.schemaVersion).toBe(2);
    expect(data.availableYears).toEqual(YEARS);
    for (const obsolete of [
      "baseThresholds",
      "metroAreas",
      "releaseMetadata",
      "nowcast",
      "nowcastEvaluation",
      "acsLookup",
      "housingSharesByYear",
      "housingShareProvenanceByYear",
    ]) {
      expect(data).not.toHaveProperty(obsolete);
    }
    expect(data.forecast.method).toBe("rolling_ce_acs_v1");
    for (const obsolete of ["factorsByYear", "thresholdsByYear"])
      expect(data.forecast).not.toHaveProperty(obsolete);
    const artifact = readCanonicalArtifact();
    expect(data.forecast.contentSha256).toBe(artifact.content_sha256);
    for (const [scenarioId, scenario] of Object.entries(
      data.forecast.scenarios,
    )) {
      expect(Object.keys(scenario.years).map(Number)).toEqual(YEARS);
      for (const year of YEARS) {
        const entry = scenario.years[year];
        const canonical = artifact.scenarios[scenarioId].years[year];
        for (const key of [
          "thresholds",
          "housing_shares",
          "rent_indices",
          "national_status",
          "housing_share_status",
          "national_source_ids",
          "housing_share_source_ids",
        ]) {
          expect(entry[key], `${scenarioId}/${year}/${key}`).toEqual(
            canonical[key],
          );
        }
        for (const key of ["ce_window", "acs_window"]) {
          for (const field of [
            "start",
            "end",
            "observed_years",
            "projected_years",
          ]) {
            expect(
              entry[key][field],
              `${scenarioId}/${year}/${key}/${field}`,
            ).toBe(canonical[key][field]);
          }
        }
      }
    }
  });

  it("uses published 2025 BLS thresholds and shares alongside modeled geography", async () => {
    const data = readCalculatorData();
    const entry =
      data.forecast.scenarios[data.forecast.defaultScenario].years[2025];
    expect(entry.thresholds.renter).toBe(41700.555713);
    expect(entry.housing_shares.renter).toBe(0.4336857704);
    expect(entry.housing_share_status).toBe("published_anchor");
    expect(entry.housing_share_source_ids).toContain("bls-spm-shares");
    render(<CalculatorWorkbench data={data} />);
    expect(screen.getByText("Published by BLS")).toBeTruthy();
    expect(screen.getByText("Housing shares published by BLS")).toBeTruthy();
    expect(screen.getByText("Modeled geography")).toBeTruthy();
    const footnote = screen.getByTestId("results-footnote");
    expect(footnote).toContainElement(
      screen.getByTestId("forecast-provenance"),
    );
    expect(footnote).toHaveTextContent(data.forecast.contentSha256);
    expect(footnote).toHaveTextContent(data.forecast.assumptionSha256);
    expect(footnote).not.toHaveTextContent(/carried forward|nowcast/i);
    expect(readFileSync("app/layout.jsx", "utf8")).not.toMatch(
      /releaseMetadata|packageVersion|informationDate|contentSha256|forecast-provenance|assumptionSha256|load_forecast/,
    );
  });

  // Keep each year's full menu audit within its own test budget on slower CI.
  it.each(YEARS)("offers all 349 estimation groups offline with correct published membership in %i", async (year) => {
    const fetch = vi.fn(() => {
      throw new Error("Network unavailable");
    });
    vi.stubGlobal("fetch", fetch);
    const data = readCalculatorData();
    render(<CalculatorWorkbench data={data} />);
    selectYear(year);
    const areas = data.areasByYear[year];
    const entries = Object.values(areas);
    const geography = Object.values(
      data.forecast.scenarios[data.forecast.defaultScenario].years[year]
        .geography_by_area,
    );
    expect(
      geography.filter((area) => area.official_published_area),
    ).toHaveLength(year === 2022 ? 342 : 341);
    expect(
      geography.filter((area) => !area.official_published_area),
    ).toHaveLength(year === 2022 ? 7 : 8);
    expect(new Set(entries.map((area) => area.area_type))).toEqual(
      new Set([
        "msa",
        "state_metro_residual",
        "modeled_residual_metro",
        "state_nonmetro",
      ]),
    );
    const search = screen.getByLabelText("Search SPM areas");
    fireEvent.focus(search);
    const menu = screen.getByRole("listbox", { name: "Matching SPM areas" });
    expect(menu).toBeVisible();
    // Check visibility once for the list, avoiding thousands of repeated
    // jsdom ancestor-style checks while verifying every annual option ID.
    expect(
      within(menu)
        .getAllByRole("option", { hidden: true })
        .map((option) => option.getAttribute("data-value"))
        .sort(),
    ).toEqual(Object.keys(areas).sort());
    fireEvent.keyDown(search, { key: "Escape", code: "Escape" });
    expect(
      screen.queryByRole("listbox", { name: "Matching SPM areas" }),
    ).toBeNull();
    for (const scenario of Object.values(data.forecast.scenarios)) {
      expect(Object.keys(scenario.years[year].rent_indices).sort()).toEqual(
        Object.keys(areas).sort(),
      );
      expect(
        Object.keys(scenario.years[year].geography_by_area).sort(),
      ).toEqual(Object.keys(areas).sort());
    }
    selectArea("1002");
    expect(
      screen.getByRole("heading", { name: "Alabama Nonmetro" }),
    ).toBeTruthy();
    expect(screen.getByText(/geography_id="1002"/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("handles the actual 2022 South Carolina Metro transition without silently choosing a replacement", async () => {
    const data = readCalculatorData();
    expect(data.areasByYear[2022]).toHaveProperty("45001");
    expect(data.areasByYear[2022]).not.toHaveProperty(
      "modeled_residual_metro:45",
    );
    expect(data.areasByYear[2023]).not.toHaveProperty("45001");
    expect(data.areasByYear[2023]).toHaveProperty("modeled_residual_metro:45");
    render(<CalculatorWorkbench data={data} />);
    selectYear(2022);
    selectArea("45001");
    expect(
      screen.getByRole("heading", { name: "South Carolina Metro" }),
    ).toBeTruthy();
    expect(screen.getByText("2022 published geography anchor")).toBeTruthy();
    selectYear(2023);
    const search = screen.getByLabelText("Search SPM areas");
    expect(search).toHaveValue("");
    expect(search).toHaveAttribute(
      "placeholder", "Selected area unavailable in 2023",
    );
    expect(screen.getByTestId("primary-result")).toHaveTextContent(
      "Unavailable",
    );
    selectArea("modeled_residual_metro:45");
    expect(screen.getByText("Modeled geography")).toBeTruthy();
    expect(screen.queryByText("2023 published geography anchor")).toBeNull();
    expect(screen.getByTestId("primary-result")).not.toHaveTextContent(
      "Unavailable",
    );
  });

  it("labels official and unpublished residual groups by their own geography status", async () => {
    const data = readCalculatorData();
    render(<CalculatorWorkbench data={data} />);
    selectYear(2024);
    const [id, area] = Object.entries(data.areasByYear[2024]).find(
      ([, area]) => area.name === "Alaska Metro",
    );
    selectArea(id);
    expect(screen.getByRole("heading", { name: area.name })).toBeTruthy();
    expect(screen.getByText("2024 published geography anchor")).toBeTruthy();
    selectArea("modeled_residual_metro:01");
    expect(screen.getByText("Modeled geography")).toBeTruthy();
    expect(screen.queryByText("2024 published geography anchor")).toBeNull();
    expect(screen.getByText("Housing shares published by BLS")).toBeTruthy();
  });

  it("does not calculate a zero-dollar threshold for unsupported adult counts", async () => {
    render(<CalculatorWorkbench data={readCalculatorData()} />);
    const adults = screen.getByLabelText("Adults");
    expect(adults.min).toBe("1");
    fireEvent.change(adults, { target: { value: "0" } });
    if (adults.value === "0") {
      expect(
        screen
          .getAllByRole("alert")
          .some((alert) => alert.textContent.includes("classified SPM adult")),
      ).toBe(true);
      expect(screen.getByTestId("primary-result")).not.toHaveTextContent("$0");
    } else {
      expect(Number(adults.value)).toBeGreaterThanOrEqual(1);
    }
  });

  it("replays every actual year and the complete comparison table in both scenarios", () => {
    const data = readCalculatorData();
    const artifact = readCanonicalArtifact();
    const currency = (value) =>
      new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
    render(<CalculatorWorkbench data={data} />);
    selectArea("35620");
    for (const [scenarioId, scenario] of Object.entries(artifact.scenarios)) {
      selectYear(2026);
      fireEvent.change(screen.getByLabelText("Real spending"), {
        target: { value: scenarioId },
      });
      const table = within(screen.getByTestId("year-by-year-card")).getByRole(
        "table",
        { hidden: true },
      );
      const rows = within(table).getAllByRole("row", { hidden: true }).slice(1);
      expect(rows).toHaveLength(YEARS.length);
      for (const year of YEARS) {
        const entry = scenario.years[year];
        const adjustment =
          1 + entry.housing_shares.renter * (entry.rent_indices["35620"] - 1);
        const expected = entry.thresholds.renter * adjustment;
        const row = rows.find(
          (candidate) =>
            within(candidate).getAllByRole("cell", { hidden: true })[0]
              .textContent === String(year),
        );
        expect(row).toHaveTextContent(currency(entry.thresholds.renter));
        expect(row).toHaveTextContent(adjustment.toFixed(3));
        expect(row).toHaveTextContent(currency(expected));
        selectYear(year);
        expect(screen.getByTestId("primary-result")).toHaveTextContent(
          currency(expected),
        );
      }
    }
  });

  it("shows the actual Massachusetts published series break in historical and future selections", () => {
    const data = readCalculatorData();
    const artifact = readCanonicalArtifact();
    render(<CalculatorWorkbench data={data} />);
    selectArea("25002");
    for (const scenarioId of Object.keys(artifact.scenarios)) {
      selectYear(2026);
      fireEvent.change(screen.getByLabelText("Real spending"), {
        target: { value: scenarioId },
      });
      const disclosure =
        artifact.scenarios[scenarioId].years[2022].geography_by_area["25002"]
          .series_breaks[0];
      expect(disclosure.from_rent_index).toBe(1.551);
      expect(disclosure.to_rent_index).toBe(1.043);
      for (const year of [2022, 2023, 2036]) {
        selectYear(year);
        const warning = screen.getByTestId("historical-series-break-warning");
        expect(warning).toBeVisible();
        expect(warning).toHaveTextContent("2022→2023");
        expect(warning).toHaveTextContent(disclosure.interpretation);
        expect(warning).toHaveTextContent(/not estimated annual rent growth/);
      }
    }
  });

  it("shows the actual Sumter published-to-modeled series break on both area identities", () => {
    const data = readCalculatorData();
    const artifact = readCanonicalArtifact();
    render(<CalculatorWorkbench data={data} />);
    for (const scenarioId of Object.keys(artifact.scenarios)) {
      selectYear(2026);
      fireEvent.change(screen.getByLabelText("Real spending"), {
        target: { value: scenarioId },
      });
      const disclosure =
        artifact.scenarios[scenarioId].years[2022].geography_by_area["45001"]
          .series_breaks[0];
      for (const [year, areaId] of [
        [2022, "45001"],
        [2023, "modeled_residual_metro:45"],
        [2036, "modeled_residual_metro:45"],
      ]) {
        selectYear(year);
        selectArea(areaId);
        const warning = screen.getByTestId("historical-series-break-warning");
        expect(warning).toBeVisible();
        expect(warning).toHaveTextContent("2022→2023");
        expect(warning).toHaveTextContent(disclosure.interpretation);
        expect(warning).toHaveTextContent("Sumter County, South Carolina");
        expect(warning).toHaveTextContent(
          /not be interpreted as annual rent growth/,
        );
      }
    }
  });

  it.each(YEARS.filter((year) => year >= 2026))(
    "reproduces %i from annual canonical inputs and exposes unsupported forecast horizons",
    (year) => {
      const data = readCalculatorData();
      render(<CalculatorWorkbench data={data} />);
      expect(
        screen.getByRole("option", { name: `${year} (forecast)` }),
      ).toBeTruthy();
      selectYear(year);
      const entry =
        data.forecast.scenarios[data.forecast.defaultScenario].years[year];
      const areaId = "35620";
      expect(screen.getByLabelText("Search SPM areas")).toHaveValue(
        data.areasByYear[year][areaId].name,
      );
      const threshold =
        entry.thresholds.renter *
        (1 + entry.housing_shares.renter * (entry.rent_indices[areaId] - 1));
      const formatted = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(threshold);
      expect(screen.getByTestId("primary-result")).toHaveTextContent(formatted);
      expect(screen.getByTestId("forecast-disclaimer")).toHaveTextContent(
        /not BLS or CBO forecasts/i,
      );
      expect(screen.getByText("Modeled geography")).toBeTruthy();
      expect(screen.getByText("Modeled housing shares")).toBeTruthy();
      expect(screen.queryByText("Published by BLS")).toBeNull();
      const snippet = screen.getByText(
        /from spm_calculator import/,
      ).textContent;
      expect(snippet).toContain(
        `load_forecast(expected_sha256="${data.forecast.contentSha256}")`,
      );
      expect(snippet).toContain(`year=${year}`);
      expect(snippet).toContain(`scenario="${data.forecast.defaultScenario}"`);
      expect(snippet).not.toMatch(
        /load_release|inflation_factor|nowcast|county|congressional/i,
      );
      if (year >= 2035) {
        const spending = year - 2025;
        expect(screen.getByTestId("ce-validation-warning")).toHaveTextContent(
          new RegExp(
            `${spending}-year spending horizon.*no retrospective backtest support`,
            "i",
          ),
        );
        expect(screen.getByTestId("acs-validation-warning")).toHaveTextContent(
          new RegExp(
            `${spending + 1}-year geographic horizon.*no retrospective backtest support`,
            "i",
          ),
        );
      }
    },
  );
});
