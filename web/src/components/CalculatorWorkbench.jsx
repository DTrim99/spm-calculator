"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  DashboardShell,
  Command,
  CommandInput,
  CommandList,
  CommandItem,
  CommandEmpty,
  SidebarLayout,
  InputPanel,
  ResultsPanel,
  SidebarSection,
  SidebarDivider,
  SelectInput,
  Input,
  Button,
  Tabs,
  TabsList,
  TabsTrigger,
  MetricCard,
  DataTable,
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  Badge,
  Text,
  Title,
  Separator,
} from "@policyengine/ui-kit";

import CalculatorSetup from "./CalculatorSetup";
import ThresholdHistoryChart from "./ThresholdHistoryChart";
import { calculateGeoadj } from "@/lib/geoadj";
import { paperPageUrl, paperPdfUrl } from "@/src/paperVersion";
import {
  ForecastMethodology,
  ForecastWarnings,
  RentalDataNote,
} from "./ForecastDiagnostics";

const TENURE_OPTIONS = [
  { value: "renter", label: "Renter" },
  { value: "owner_with_mortgage", label: "Mortgage" },
  { value: "owner_without_mortgage", label: "No mortgage" },
];

const TENURE_LABELS = {
  renter: "Renter",
  owner_with_mortgage: "Owner with mortgage",
  owner_without_mortgage: "Owner without mortgage",
};

const PYPI_URL = "https://pypi.org/project/spm-calculator/";
const GITHUB_URL = "https://github.com/PolicyEngine/spm-calculator";
const PREVIEW_URL = `${GITHUB_URL}/pull/51`;

function fmtCurrency(value, fractionDigits = 0) {
  if (!Number.isFinite(value)) return "Unavailable";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: fractionDigits,
    minimumFractionDigits: fractionDigits,
  }).format(value);
}

function fmtPercent(value) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}%`;
}

function fmtInput(value) {
  return Number.isFinite(value) ? value.toFixed(3) : "Unavailable";
}

export function getRawEquivalenceScale(adults, children, methodology) {
  // Child-only ("0 adults, N children") units aren't valid SPM households,
  // so we return 0 rather than synthesising a single-parent scale from a
  // ghost adult. This matches the Python helper in
  // `spm_calculator.equivalence_scale.spm_equivalence_scale`.
  if (adults === 0) return 0;

  if (children > 0) {
    if (adults === 1) {
      return (
        (1 +
          methodology.equivalenceScale.singleAdultFirstChild +
          methodology.equivalenceScale.additionalChild *
            Math.max(children - 1, 0)) **
        methodology.equivalenceScale.economiesOfScale
      );
    }

    return (
      (adults + methodology.equivalenceScale.additionalChild * children) **
      methodology.equivalenceScale.economiesOfScale
    );
  }

  if (adults === 1) return 1;
  if (adults === 2) return methodology.equivalenceScale.twoAdultNoChild;
  return adults ** methodology.equivalenceScale.economiesOfScale;
}

function describeEquivalenceFormula(adults, children, methodology) {
  const {
    singleAdultFirstChild,
    additionalChild,
    economiesOfScale,
    twoAdultNoChild,
  } = methodology.equivalenceScale;
  if (adults === 0 && children === 0) return "0";
  if (children > 0) {
    if (adults <= 1) {
      return `(1 + ${singleAdultFirstChild} + ${additionalChild} * (${children} - 1))^${economiesOfScale}`;
    }
    return `(${adults} + ${additionalChild} * ${children})^${economiesOfScale}`;
  }
  if (adults <= 1) return "1.0";
  if (adults === 2) return String(twoAdultNoChild);
  return `${adults}^${economiesOfScale}`;
}

function rollingAdjustment(entry, areaId, tenure) {
  if (!entry?.geography_by_area?.[areaId]) return null;
  const rentIndex = entry?.rent_indices?.[areaId];
  const housingShare = entry?.housing_shares?.[tenure];
  if (
    !Number.isFinite(rentIndex) ||
    rentIndex <= 0 ||
    !Number.isFinite(housingShare) ||
    housingShare <= 0 ||
    housingShare >= 1
  )
    return null;
  return calculateGeoadj({ rentIndex, housingShare });
}

function ControlSection({ title, inSidebar, children }) {
  if (inSidebar) {
    return <SidebarSection title={title}>{children}</SidebarSection>;
  }
  return (
    <fieldset className="min-w-0 space-y-3">
      {title && (
        <legend className="text-sm font-medium text-foreground">{title}</legend>
      )}
      {children}
    </fieldset>
  );
}

export default function CalculatorWorkbench({ data }) {
  const {
    methodology: baseMethodology,
    forecast,
    areasByYear,
    packageVersion,
    packageDistribution,
  } = data;
  const [setupComplete, setSetupComplete] = useState(false);
  const [advanceRequest, setAdvanceRequest] = useState(null);
  const resultHeadingRef = useRef(null);
  useEffect(() => {
    if (setupComplete) resultHeadingRef.current?.focus();
  }, [setupComplete]);
  const latestPublishedYear = forecast.latestPublishedYear;
  const [scenarioId, setScenarioId] = useState(forecast.defaultScenario);
  const selectedScenario = forecast.scenarios[scenarioId];
  const availableYears = Object.keys(selectedScenario.years).sort(
    (left, right) => Number(right) - Number(left),
  );
  const [year, setYear] = useState("");
  const selectedEntry = selectedScenario.years[year];
  // Offer every supported area before the user chooses a year. Once chosen,
  // use that year's actual coverage and revalidate the selected area.
  const areas = useMemo(
    () =>
      year
        ? (areasByYear[year] ?? {})
        : Object.assign(
            {},
            ...[...availableYears]
              .reverse()
              .map((value) => areasByYear[value] ?? {}),
          ),
    [year, areasByYear, selectedScenario],
  );
  const metroEntries = useMemo(
    () =>
      Object.entries(areas)
        .filter(([id]) => !year || selectedEntry?.geography_by_area?.[id])
        .sort(([, left], [, right]) => left.name.localeCompare(right.name)),
    [areas, selectedEntry, year],
  );
  const methodology = {
    ...baseMethodology,
    housingShares: selectedEntry?.housing_shares ?? {},
  };
  const [numAdults, setNumAdults] = useState("");
  const [numChildren, setNumChildren] = useState("");
  const [tenure, setTenure] = useState("");
  const [selectedGeographyId, setSelectedGeographyId] = useState("");
  const [locationQuery, setLocationQuery] = useState("");
  const [locationEditing, setLocationEditing] = useState(false);
  const locationNavigated = useRef(false);
  const locationInputRef = useRef(null);
  const pendingLocationEdit = useRef(null);
  const selectedAreaStatus =
    selectedEntry?.geography_by_area?.[selectedGeographyId];
  const selectedArea =
    !year || selectedAreaStatus ? (areas[selectedGeographyId] ?? null) : null;
  const currentLocation = selectedArea
    ? { id: selectedGeographyId, label: selectedArea.name }
    : null;
  const yearIsForecast = selectedEntry?.national_status === "forecast";
  const geographyPublished = selectedAreaStatus?.status === "published_anchor";
  const sharesPublished =
    selectedEntry?.housing_share_status === "published_anchor";
  const publishedPackage =
    packageDistribution?.status === "published" &&
    packageDistribution.version === packageVersion &&
    packageDistribution.publishedVersion === packageVersion;
  const packageUrl = publishedPackage
    ? `${PYPI_URL}${packageVersion}/`
    : PREVIEW_URL;
  const packageLabel = publishedPackage
    ? `spm-calculator ${packageVersion}`
    : "Preview Python API: calculator PR36";
  const ceSurveyWindow = selectedEntry?.ce_window
    ? `${selectedEntry.ce_window.start}–${selectedEntry.ce_window.end}`
    : "Unavailable";
  const annualForecastAssumptions = Object.entries(
    forecast.cpiProjections ?? {},
  )
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([target, rate]) => `${target}: ${(rate * 100).toFixed(1)}%`)
    .join("; ");
  const filteredMetroEntries = useMemo(() => {
    const query = locationQuery.trim().toLowerCase();
    return query
      ? metroEntries.filter(([id, area]) =>
          `${id} ${area.name}`.toLowerCase().includes(query),
        )
      : metroEntries;
  }, [locationQuery, metroEntries]);
  const base = selectedEntry?.thresholds?.[tenure] ?? null;
  const compositionValid =
    Number.isInteger(numAdults) &&
    numAdults >= 1 &&
    Number.isInteger(numChildren) &&
    numChildren >= 0;
  const rawScale = compositionValid
    ? getRawEquivalenceScale(numAdults, numChildren, methodology)
    : null;
  const householdValid = compositionValid && Boolean(TENURE_LABELS[tenure]);
  const equivalenceScale = compositionValid
    ? rawScale / methodology.equivalenceScale.referenceFamilyRaw
    : null;
  function areaAdjustment(areaTenure) {
    return selectedArea
      ? rollingAdjustment(selectedEntry, selectedGeographyId, areaTenure)
      : null;
  }
  const geoadj = areaAdjustment(tenure);
  const rollingDataUnavailable =
    !selectedArea || geoadj === null || !Number.isFinite(base) || base <= 0;
  const locationError =
    !year || !selectedGeographyId
      ? ""
      : !selectedArea
        ? `The selected area is unavailable in ${year}. Choose an area available for this year.`
        : tenure && rollingDataUnavailable
          ? "Rolling forecast data is unavailable for this area, tenure and year."
          : "";
  const threshold =
    rollingDataUnavailable || !compositionValid
      ? null
      : base * equivalenceScale * geoadj;
  const monthlyThreshold = threshold === null ? null : threshold / 12;
  const thresholdVsReference =
    threshold === null ? null : ((threshold - base) / base) * 100;
  const officialReferenceThreshold = rollingDataUnavailable
    ? null
    : base * geoadj;
  const selectedTenureLabel = TENURE_LABELS[tenure] ?? "Choose housing tenure";
  const selectedLocationRentIndex = selectedArea
    ? selectedEntry?.rent_indices?.[selectedGeographyId]
    : null;
  const tenureComparisonData = TENURE_OPTIONS.map((option) => {
    const adjustment = areaAdjustment(option.value);
    const nationalBase = selectedEntry?.thresholds?.[option.value] ?? null;
    return {
      tenure: option.label,
      nationalBase,
      adjustment,
      locationThreshold:
        adjustment === null || !Number.isFinite(nationalBase)
          ? null
          : nationalBase * adjustment,
    };
  });
  const yearComparisonData = [...availableYears].reverse().map((targetYear) => {
    const entry = selectedScenario.years[targetYear];
    const nationalBase = entry?.thresholds?.[tenure] ?? null;
    const adjustment = areasByYear[targetYear]?.[selectedGeographyId]
      ? rollingAdjustment(entry, selectedGeographyId, tenure)
      : null;
    const areaStatus = entry?.geography_by_area?.[selectedGeographyId];
    return {
      year: targetYear,
      nationalBase,
      adjustment,
      nationalStatus: entry?.national_status,
      housingShare: entry?.housing_shares?.[tenure] ?? null,
      rentIndex: entry?.rent_indices?.[selectedGeographyId] ?? null,
      equivalenceScale: compositionValid ? equivalenceScale : null,
      componentStatus: !areaStatus
        ? "Area unavailable"
        : `${entry.national_status === "published" ? "Published national base" : "Forecast national base"}; ${areaStatus.status === "published_anchor" ? "published geography" : "modeled geography"}; ${entry.housing_share_status === "published_anchor" ? "published shares" : "modeled shares"}`,
      localThreshold:
        !compositionValid ||
        !Number.isFinite(nationalBase) ||
        nationalBase <= 0 ||
        adjustment === null
          ? null
          : nationalBase * equivalenceScale * adjustment,
    };
  });
  const packageSnippet = !compositionValid
    ? "# Enter valid classified adult and child counts to generate a calculation."
    : rollingDataUnavailable
      ? "# Rolling forecast data is unavailable for the selected area, tenure and year."
      : `from spm_calculator import load_forecast, SPMUnit

projection = load_forecast(expected_sha256="${forecast.contentSha256}")
result = projection.calculate_unit(SPMUnit(
    unit_id="household-1",
    num_adults=${numAdults}, num_children=${numChildren},
    tenure="${tenure}", year=${year},
    geography_kind="metro",
    geography_id="${currentLocation.id}",
), scenario="${scenarioId}")
print(result["threshold"])`;
  const yearSelectOptions = availableYears.map((value) => ({
    value,
    label: `${value}${selectedScenario.years[value].national_status === "forecast" ? " (forecast)" : ""}`,
  }));

  // ── Render ──────────────────────────────────────────────────

  function editLocationSearch(query) {
    pendingLocationEdit.current = null;
    locationNavigated.current = false;
    setLocationQuery(query);
    setLocationEditing(true);
  }

  function openLocationSearch() {
    if (!locationEditing) editLocationSearch("");
  }

  function closeLocationSearch() {
    pendingLocationEdit.current = null;
    locationNavigated.current = false;
    setLocationQuery("");
    setLocationEditing(false);
  }

  function selectLocation(code) {
    setSelectedGeographyId(code);
    locationInputRef.current?.focus();
    closeLocationSearch();
    if (!setupComplete) setAdvanceRequest({ stepId: "location" });
  }

  function selectSetupYear(value) {
    setYear(value);
    setAdvanceRequest({ stepId: "year" });
  }

  const yearControls = !setupComplete ? (
    <div className="space-y-6">
      {[
        ["Published national thresholds", false],
        ["Forecasts", true],
      ].map(([label, isForecast]) => (
        <fieldset key={label} className="space-y-3">
          <legend className="text-sm font-medium text-muted-foreground">
            {label}
          </legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {availableYears
              .filter(
                (value) =>
                  (selectedScenario.years[value].national_status ===
                    "forecast") ===
                  isForecast,
              )
              .sort((a, b) => Number(a) - Number(b))
              .map((value) => (
                <Button
                  key={value}
                  type="button"
                  variant="outline"
                  aria-label={`${value}${isForecast ? " (forecast)" : ""}`}
                  aria-pressed={year === value}
                  onClick={() => selectSetupYear(value)}
                  className="h-auto min-h-14 flex-col gap-1 py-3"
                >
                  <span>{value}</span>
                  {Number(value) === latestPublishedYear && (
                    <span className="text-xs font-normal text-muted-foreground">
                      Latest published
                    </span>
                  )}
                </Button>
              ))}
          </div>
        </fieldset>
      ))}
    </div>
  ) : (
    <>
      <SidebarSection title="Threshold year">
        <SelectInput
          id="spm-year"
          aria-label="Threshold year"
          options={yearSelectOptions}
          value={year}
          onChange={setYear}
        />
      </SidebarSection>

      {yearIsForecast && (
        <SidebarSection title="Real spending">
          <SelectInput
            id="spm-scenario"
            aria-label="Real spending"
            options={Object.entries(forecast.scenarios ?? {}).map(
              ([value, scenario]) => ({ value, label: scenario.label }),
            )}
            value={scenarioId}
            onChange={setScenarioId}
          />
        </SidebarSection>
      )}
    </>
  );
  const householdControls = (
    <>
      {!compositionValid &&
        (numAdults !== "" || numChildren !== "") &&
        ((numAdults !== "" &&
          (!Number.isInteger(numAdults) || numAdults < 1)) ||
          (numChildren !== "" &&
            (!Number.isInteger(numChildren) || numChildren < 0))) && (
          <p role="alert">
            Enter at least one classified SPM adult and a nonnegative whole
            number of children. Minor-only units need a separate classification
            decision.
          </p>
        )}
      <ControlSection
        inSidebar={setupComplete}
        title={setupComplete ? "Household composition" : undefined}
      >
        <div className="flex gap-3">
          {[
            ["Adults", "spm-adults", numAdults, setNumAdults, 1, 12],
            ["Children", "spm-children", numChildren, setNumChildren, 0, 16],
          ].map(([label, id, value, setValue, min, max]) => (
            <div key={id} className="flex min-w-0 flex-1 flex-col gap-1">
              <label
                htmlFor={id}
                className="text-sm font-medium text-muted-foreground"
              >
                {label}
              </label>
              <Input
                id={id}
                className={
                  setupComplete ? undefined : "h-12 bg-background text-base"
                }
                type="number"
                inputMode="numeric"
                value={value}
                onChange={(event) =>
                  setValue(
                    event.target.value === "" ? "" : Number(event.target.value),
                  )
                }
                min={min}
                max={max}
                step={1}
                required
              />
            </div>
          ))}
        </div>
      </ControlSection>

      {setupComplete && <SidebarDivider />}

      <ControlSection
        inSidebar={setupComplete}
        title={setupComplete ? "Housing tenure" : "How do you pay for housing?"}
      >
        <Tabs value={tenure} onValueChange={setTenure} activationMode="manual">
          <TabsList
            aria-label="Housing tenure"
            className={
              setupComplete
                ? undefined
                : "h-auto w-full gap-2 bg-transparent p-0"
            }
          >
            {TENURE_OPTIONS.map((option) => (
              <TabsTrigger
                key={option.value}
                value={option.value}
                className={
                  setupComplete
                    ? undefined
                    : "min-h-12 flex-1 border border-border bg-background data-[state=active]:border-primary data-[state=active]:bg-primary/5 data-[state=active]:text-primary"
                }
              >
                {option.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </ControlSection>
    </>
  );
  const locationControls = (
    <>
      <ControlSection
        inSidebar={setupComplete}
        title={setupComplete ? "Location" : undefined}
      >
        <div className="space-y-3">
          <Command
            label="Search SPM areas"
            shouldFilter={false}
            onMouseDown={(event) => {
              if (event.target !== locationInputRef.current) {
                event.preventDefault();
                locationInputRef.current?.focus();
                openLocationSearch();
              }
            }}
            onKeyDown={(event) => {
              if (
                event.defaultPrevented ||
                event.nativeEvent.isComposing ||
                event.nativeEvent.keyCode === 229
              ) {
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                locationInputRef.current?.focus();
                closeLocationSearch();
              } else if (
                event.key === "Enter" &&
                !locationQuery.trim() &&
                !locationNavigated.current
              ) {
                // cmdk highlights the first option on mount. An untouched
                // search is not an explicit choice, including from the listbox.
                event.preventDefault();
                locationInputRef.current?.focus();
                closeLocationSearch();
              } else if (
                event.key === "ArrowDown" ||
                event.key === "ArrowUp"
              ) {
                if (!locationEditing) {
                  event.preventDefault();
                  openLocationSearch();
                }
                locationNavigated.current = true;
              } else if (
                locationEditing &&
                (event.key === "Home" ||
                  event.key === "End" ||
                  (event.ctrlKey && ["n", "j", "p", "k"].includes(event.key)))
              ) {
                // Preserve cmdk's navigation behavior for all input and list
                // targets, accepting the highlighted choice on a later Enter.
                locationNavigated.current = true;
              }
            }}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) {
                closeLocationSearch();
              }
            }}
            className="h-auto rounded-lg border border-border bg-background transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/15 motion-reduce:transition-none [&_[data-slot=command-input-wrapper]]:h-12 [&_[data-slot=command-input-wrapper]]:border-0 [&_[data-slot=command-input-wrapper]]:px-4"
          >
            <CommandInput
              asChild
              ref={locationInputRef}
              placeholder={
                setupComplete && !selectedArea && !locationEditing
                  ? `Selected area unavailable in ${year}`
                  : "Search a metro area or state"
              }
              className="h-12 text-ellipsis py-0 text-base"
              value={
                locationEditing ? locationQuery : (selectedArea?.name ?? "")
              }
              title={!locationEditing ? selectedArea?.name : undefined}
              onFocus={openLocationSearch}
              onClick={openLocationSearch}
              onValueChange={(value) => {
                // Some soft keyboards append directly to the displayed name
                // without sending compositionstart or an editable keydown.
                const name = selectedArea?.name;
                const query = pendingLocationEdit.current ?? (
                  !locationEditing && name && value.startsWith(name)
                    ? value.slice(name.length)
                    : value
                );
                editLocationSearch(query);
              }}
              onPaste={(event) => {
                if (!locationEditing) {
                  event.preventDefault();
                  editLocationSearch(event.clipboardData.getData("text"));
                }
              }}
              onCompositionStart={() => {
                if (!locationEditing) {
                  editLocationSearch("");
                }
              }}
              onKeyDown={(event) => {
                if (
                  event.nativeEvent.isComposing ||
                  event.nativeEvent.keyCode === 229
                ) {
                  return;
                }
                if (
                  !locationEditing &&
                  !event.ctrlKey &&
                  !event.metaKey &&
                  !event.altKey &&
                  (event.key.length === 1 ||
                    event.key === "Backspace" ||
                    event.key === "Delete")
                ) {
                  // Selection leaves the input focused with the committed name
                  // visible. The first edit must begin a new query, not append
                  // to that name; subsequent edits use the normal input event.
                  event.preventDefault();
                  editLocationSearch(event.key.length === 1 ? event.key : "");
                }
              }}
            >
              {/* cmdk assumes an always-open palette. Its input slot lets this
                  collapsible selector expose its actual state to screen readers. */}
              <input
                aria-expanded={locationEditing}
                onInputCapture={(event) => {
                  pendingLocationEdit.current = null;
                  if (locationEditing) return;
                  const { inputType, data } = event.nativeEvent;
                  if (inputType?.startsWith("delete")) {
                    pendingLocationEdit.current = "";
                  } else if (
                    inputType?.startsWith("insert") && typeof data === "string"
                  ) {
                    pendingLocationEdit.current = data;
                  }
                }}
                {...(!locationEditing && {
                  "aria-controls": undefined,
                  "aria-activedescendant": undefined,
                })}
              />
            </CommandInput>
            {locationEditing && (
              <CommandList
                label="Matching SPM areas"
                className="max-h-60 border-t border-border p-1"
              >
                <CommandEmpty>No SPM areas match your search.</CommandEmpty>
                {filteredMetroEntries.map(([code, info]) => (
                  <CommandItem
                    key={code}
                    value={code}
                    className="min-h-11 cursor-pointer px-3 py-2 leading-snug"
                    onPointerMoveCapture={() => {
                      locationNavigated.current = true;
                    }}
                    onSelect={() => selectLocation(code)}
                  >
                    {info.name}
                  </CommandItem>
                ))}
              </CommandList>
            )}
          </Command>
          {setupComplete ? (
            <p className="text-xs leading-5 text-muted-foreground">
              SPM estimation areas: MSAs, residual metro groups and state nonmetro
              groups. Availability and publication status depend on the year.
            </p>
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              Outside a metro area? Search for your state.
            </p>
          )}
        </div>
      </ControlSection>

      {locationError && (
        <div
          role="alert"
          className="mx-4 mb-4 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {locationError}
        </div>
      )}
    </>
  );

  const sidebar = (
    <InputPanel title="Adjust your inputs">
      {locationControls}
      <SidebarDivider />
      {householdControls}
      <SidebarDivider />
      {yearControls}
      <SidebarDivider />
      <SidebarSection title="Method details">
        <div className="space-y-2 text-sm text-muted-foreground">
          <div className="flex justify-between">
            <span>Data source</span>
            <span className="font-medium text-foreground">
              {year} rolling windows
            </span>
          </div>
          <div className="flex justify-between">
            <span>Housing share</span>
            <span className="font-medium text-foreground">
              {fmtInput(methodology.housingShares[tenure])}
            </span>
          </div>
          {selectedLocationRentIndex !== null && (
            <div className="flex justify-between">
              <span>Rent index</span>
              <span className="font-medium text-foreground">
                {Number.isFinite(selectedLocationRentIndex)
                  ? selectedLocationRentIndex.toFixed(3)
                  : "Unavailable"}
              </span>
            </div>
          )}
        </div>
      </SidebarSection>
    </InputPanel>
  );

  const tenureTableColumns = [
    {
      key: "tenure",
      header: "Tenure",
      format: (v) => v,
    },
    {
      key: "nationalBase",
      header: `${year} national base`,
      align: "right",
      format: (v) => fmtCurrency(v),
    },
    {
      key: "locationThreshold",
      header: "Location threshold",
      align: "right",
      format: (v) => (v === null ? "Unavailable" : fmtCurrency(v)),
    },
    {
      key: "adjustment",
      header: "Adjustment",
      align: "right",
      format: (v) => (v === null ? "Unavailable" : `\u00D7${v.toFixed(3)}`),
    },
  ];

  if (!setupComplete) {
    return (
      <DashboardShell>
        <CalculatorSetup
          advanceRequest={advanceRequest}
          steps={[
            {
              id: "location",
              title: "Where do you live?",
              shortTitle: "Location",
              description: "Choose your area to find the local poverty threshold.",
              autoAdvance: true,
              content: locationControls,
              summary: currentLocation?.label ?? "Choose an area",
              valid: Boolean(selectedArea),
            },
            {
              id: "household",
              title: "Who is in your household?",
              shortTitle: "Household",
              description:
                "Enter the number of adults and children, and how you pay for housing.",
              content: householdControls,
              summary: householdValid
                ? `${numAdults} adults, ${numChildren} children · ${selectedTenureLabel}`
                : "Enter your household details",
              valid: householdValid,
            },
            {
              id: "year",
              title: "Which year?",
              shortTitle: "Year",
              description:
                "Select a year to view your threshold. You can change forecast assumptions in the results.",
              autoAdvance: true,
              content: yearControls,
              summary: year
                ? `${year}${yearIsForecast ? ` · ${selectedScenario.label}` : " · Published national thresholds"}`
                : "Choose a year",
              valid: Boolean(selectedEntry),
            },
          ]}
          onComplete={() => setSetupComplete(true)}
        />
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <SidebarLayout sidebar={sidebar} sidebarWidth="320px">
        <ResultsPanel>
          <div className="min-w-0 space-y-6">
            {/* Primary result */}
            <div data-testid="primary-result">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Title
                  ref={resultHeadingRef}
                  order={2}
                  tabIndex={-1}
                  className="scroll-mt-24 text-xl focus:outline-none"
                >
                  {currentLocation?.label ?? "Area unavailable"}
                </Title>
                <Badge variant="secondary">{selectedTenureLabel}</Badge>
                {yearIsForecast && (
                  <Badge variant="warning">Research forecast</Badge>
                )}
                {selectedAreaStatus && (
                  <Badge variant={geographyPublished ? "secondary" : "warning"}>
                    {geographyPublished
                      ? `${year} published geography anchor`
                      : "Modeled geography"}
                  </Badge>
                )}
                {selectedEntry && (
                  <Badge variant={sharesPublished ? "secondary" : "warning"}>
                    {sharesPublished
                      ? "Housing shares published by BLS"
                      : "Modeled housing shares"}
                  </Badge>
                )}
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label="SPM threshold"
                  value={fmtCurrency(threshold)}
                  format="string"
                />
                <MetricCard
                  label="Monthly"
                  value={
                    monthlyThreshold === null
                      ? "Unavailable"
                      : fmtCurrency(monthlyThreshold)
                  }
                  format="string"
                />
                <MetricCard
                  label="vs. national ref."
                  value={
                    thresholdVsReference === null
                      ? "Unavailable"
                      : fmtPercent(thresholdVsReference)
                  }
                  format="string"
                  trend={
                    thresholdVsReference === null
                      ? "neutral"
                      : thresholdVsReference > 0
                        ? "negative"
                        : "positive"
                  }
                />
                <MetricCard
                  label="Location factor"
                  value={fmtInput(geoadj)}
                  format="string"
                />
              </div>
              {
                <ForecastWarnings
                  forecast={forecast}
                  scenarioId={scenarioId}
                  year={year}
                  entry={selectedEntry}
                  areaId={selectedGeographyId}
                />
              }
            </div>

            {
              <Card data-testid="year-by-year-card" className="min-w-0">
                <CardHeader>
                  <CardTitle>Year by year</CardTitle>
                  <CardDescription>
                    {currentLocation?.label} · {selectedTenureLabel} ·{" "}
                    {numAdults} adults, {numChildren} children · Forecast
                    spending: {selectedScenario?.label}. National bases are for
                    two adults and two children; local thresholds use your
                    household.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <ThresholdHistoryChart
                    data={yearComparisonData}
                    selectedYear={year}
                    onSelectYear={(value) => setYear(String(value))}
                  />
                  <p className="mt-3 text-xs text-muted-foreground">
                    National thresholds and housing shares are published for
                    2022–2025. Geography status is specific to each area and
                    year. From 2026, values are conditional rolling CE and ACS
                    research forecasts.
                  </p>
                </CardContent>
              </Card>
            }

            <Separator />

            {/* Breakdown cards */}
            <div className="grid gap-4 lg:grid-cols-3">
              <Card>
                <CardHeader>
                  <CardTitle>Base threshold</CardTitle>
                  <CardDescription>
                    {yearIsForecast
                      ? "Projected national reference-family threshold before adjustments"
                      : "National BLS reference-family threshold before adjustments"}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">Tenure</Text>
                      <Text className="font-medium">{selectedTenureLabel}</Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">
                        Base threshold
                      </Text>
                      <Text className="font-medium">{fmtCurrency(base)}</Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">Status</Text>
                      <Badge
                        variant={yearIsForecast ? "warning" : "secondary"}
                        className="text-xs"
                      >
                        {yearIsForecast ? "Forecast" : "Published by BLS"}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Family size</CardTitle>
                  <CardDescription>
                    Betson equivalence scale normalized to the 2A2C reference
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">Formula</Text>
                      <Text className="font-mono text-xs font-medium">
                        {compositionValid
                          ? describeEquivalenceFormula(
                              numAdults,
                              numChildren,
                              methodology,
                            )
                          : "Unavailable"}
                      </Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">
                        Normalized scale
                      </Text>
                      <Text className="font-medium">
                        {equivalenceScale?.toFixed(3) ?? "Unavailable"}
                      </Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">Household</Text>
                      <Text className="font-medium">
                        {compositionValid
                          ? `${numAdults} adult${numAdults === 1 ? "" : "s"}, ${numChildren} child${numChildren === 1 ? "" : "ren"}`
                          : "Unavailable"}
                      </Text>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Location adjustment</CardTitle>
                  <CardDescription>
                    Selected area rent index with tenure-specific housing share
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between gap-4">
                      <Text className="shrink-0 text-muted-foreground">
                        Location
                      </Text>
                      <Text className="min-w-0 text-right font-medium">
                        {currentLocation?.label ?? "Unavailable"}
                      </Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">
                        Location factor
                      </Text>
                      <Text className="font-medium">
                        {geoadj === null ? "Unavailable" : geoadj.toFixed(3)}
                      </Text>
                    </div>
                    <div className="flex justify-between">
                      <Text className="text-muted-foreground">
                        Ref. 2A2C threshold
                      </Text>
                      <Text className="font-medium">
                        {officialReferenceThreshold === null
                          ? "Unavailable"
                          : fmtCurrency(officialReferenceThreshold)}
                      </Text>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            <Separator />

            {/* Tenure comparison table */}
            <div>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <Title order={3} className="text-lg">
                  Tenure comparison
                </Title>
                <Text className="text-sm text-muted-foreground">
                  {currentLocation?.label ?? "Area unavailable"} · Two adults,
                  two children
                </Text>
              </div>
              <DataTable
                className="overflow-x-auto"
                role="region"
                aria-label="Tenure thresholds"
                tabIndex={0}
                columns={tenureTableColumns}
                data={tenureComparisonData}
                styles={{ table: { minWidth: "440px" } }}
              />
            </div>

            <Separator />

            <section
              data-testid="results-footnote"
              aria-label="Methodology and provenance"
              className="space-y-6"
            >
              <RentalDataNote
                entry={selectedEntry}
                areaId={selectedGeographyId}
              />
              {/* Methodology explainer */}
              <Card data-testid="methodology-card">
                <CardHeader>
                  <CardTitle>How this is calculated</CardTitle>
                  <CardDescription>
                    National bases, rolling CE spending and ACS rent windows
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm leading-6">
                  <div
                    data-testid="methodology-paper-actions"
                    className="flex flex-wrap gap-2"
                  >
                    <a
                      className="inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground no-underline transition-colors hover:bg-primary/90"
                      href={paperPageUrl}
                    >
                      Read the methods paper
                    </a>
                    <a
                      className="inline-flex items-center rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground no-underline transition-colors hover:bg-muted"
                      href={paperPdfUrl}
                    >
                      Download the PDF
                    </a>
                  </div>
                  <p>
                    Threshold = <code className="font-mono">base[tenure]</code>{" "}
                    × <code className="font-mono">equivalence_scale</code> ×{" "}
                    <code className="font-mono">geoadj[tenure]</code>
                  </p>
                  {
                    <div className="space-y-3">
                      <p data-testid="source-windows">
                        <strong>{year} source windows</strong>: CE{" "}
                        {ceSurveyWindow}
                        {selectedEntry?.ce_window &&
                          ` · ${selectedEntry.ce_window.observed_quarters} observed / ${selectedEntry.ce_window.projected_quarters} projected quarters`}
                        . ACS{" "}
                        {selectedEntry?.acs_window
                          ? `${selectedEntry.acs_window.start}\u2013${selectedEntry.acs_window.end} · ${selectedEntry.acs_window.observed_years} observed / ${selectedEntry.acs_window.projected_years} projected years`
                          : "Unavailable"}
                        .
                      </p>
                      {yearIsForecast && (
                        <p>
                          <strong>Common price assumptions</strong>:{" "}
                          {annualForecastAssumptions ||
                            "No price projections supplied"}
                          . <strong>Selected real spending growth</strong>:{" "}
                          {Number.isFinite(selectedScenario?.realGrowthRate)
                            ? `${(selectedScenario.realGrowthRate * 100).toFixed(2)}% per year`
                            : "Unavailable"}
                          . New observations are projected; rolling history is
                          retained. CE real spending and ACS rent assumptions
                          are separate.
                        </p>
                      )}
                      <p
                        data-testid="forecast-disclaimer"
                        className="text-muted-foreground"
                      >
                        Both spending scenarios are conditional research
                        forecasts, not BLS or CBO forecasts. National thresholds
                        and housing shares for 2022–2025 are published by BLS.
                        The 2025 geography is modeled; 2026 onward uses future
                        conditional rolling CE and ACS inputs. Census publishes
                        indices for most historical areas; residual groups
                        without a published anchor are modeled.
                      </p>
                      {selectedAreaStatus && (
                        <p data-testid="area-provenance">
                          <strong>Selected geography</strong>:{" "}
                          {selectedAreaStatus.interpretation}.
                          {selectedAreaStatus.official_published_area
                            ? " This area has a published Census geography anchor."
                            : " This residual group has no published Census geography anchor."}
                        </p>
                      )}
                    </div>
                  }
                  <ul className="list-disc space-y-1 pl-6 text-muted-foreground">
                    <li>
                      <strong>Base</strong>: BLS reference-family thresholds for
                      two adults and two children. Future thresholds use each
                      year's rolling CE window. Public CE replication is
                      approximate; forecast uncertainty is not estimated.
                    </li>
                    <li>
                      <strong>Equivalence scale</strong>: Betson
                      three-parameter. Single-adult with K children:{" "}
                      <code className="font-mono">
                        (1 + 0.8 + 0.5·(K−1))^0.7
                      </code>
                      . Multi-adult with children:{" "}
                      <code className="font-mono">(A + 0.5·K)^0.7</code>.
                      Reference 2A2C = <code className="font-mono">3^0.7</code>.
                    </li>
                    <li>
                      <strong>GEOADJ</strong>:{" "}
                      <code className="font-mono">
                        1 + housing_share * (rent_index - 1)
                      </code>
                      , using the selected year's inputs for SPM metro/nonmetro
                      estimation areas. Housing shares: renter{" "}
                      {fmtInput(methodology.housingShares.renter)}, owner with
                      mortgage{" "}
                      {fmtInput(methodology.housingShares.owner_with_mortgage)},
                      owner without mortgage{" "}
                      {fmtInput(
                        methodology.housingShares.owner_without_mortgage,
                      )}
                      . County names inside MSA labels identify metropolitan
                      areas; this app does not estimate independent county or
                      congressional district thresholds.
                    </li>
                  </ul>
                  {
                    <ForecastMethodology
                      forecast={forecast}
                      scenarioId={scenarioId}
                      year={year}
                      entry={selectedEntry}
                      areaId={selectedGeographyId}
                    />
                  }
                  <p className="text-xs text-muted-foreground">
                    Census methodology:{" "}
                    <a
                      className="underline"
                      href="https://www.bls.gov/pir/spm/garner_spm_choices_03_15_21.pdf"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Garner (2021)
                    </a>
                    . 2024 anchor workbook:{" "}
                    <a
                      className="underline"
                      href="https://www.census.gov/library/publications/2025/demo/p60-287.html"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      P60-287
                    </a>
                    . Methods:{" "}
                    <a className="underline" href={paperPageUrl}>
                      Read the methods paper
                    </a>
                    .
                  </p>
                </CardContent>
              </Card>

              {/* Python package */}
              <Card>
                <CardHeader>
                  <CardTitle>Reproduce with Python</CardTitle>
                  <CardDescription>
                    {publishedPackage
                      ? `Reproduce with published spm-calculator ${packageVersion}`
                      : `Reproduce with local build / development preview ${packageVersion}. This build is not confirmed published on PyPI.`}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-sm leading-6 text-foreground">
                    <code>{packageSnippet}</code>
                  </pre>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <a
                      className="text-primary underline"
                      href={packageUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {packageLabel}
                    </a>
                    <a
                      className="text-primary underline"
                      href={GITHUB_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      GitHub
                    </a>
                  </div>
                </CardContent>
              </Card>

              {/* Version + data vintage footer */}
              <footer
                data-testid="version-footer"
                className="pt-2 text-xs text-muted-foreground"
              >
                <p data-testid="forecast-provenance">
                  Rolling CE + ACS research forecast · information date{" "}
                  {forecast.informationDate}. Forecast SHA-256:{" "}
                  <code className="break-all">{forecast.contentSha256}</code>.
                  Base release SHA-256:{" "}
                  <code className="break-all">
                    {forecast.baseReleaseSha256}
                  </code>
                  . Assumption SHA-256:{" "}
                  <code className="break-all">{forecast.assumptionSha256}</code>
                  .
                </p>
                {forecast.auditArtifact?.url && (
                  <p className="mt-2">
                    <a
                      className="underline"
                      href={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${forecast.auditArtifact.url}`}
                      download={forecast.auditArtifact.name || true}
                    >
                      Download full canonical audit data (JSON)
                    </a>
                    . Includes the scientific inputs and diagnostics for the
                    forecast hash above.
                  </p>
                )}
                <p className="mt-2">
                  {publishedPackage
                    ? `Published package: spm-calculator ${packageVersion}.`
                    : `Local build / development preview: spm-calculator ${packageVersion}. Publication on PyPI is not confirmed for this build.`}
                </p>
                <p className="mt-2">
                  Sources:{" "}
                  {(forecast.sources ?? [])
                    .filter((source) => source.url)
                    .map((source, index) => (
                      <span key={source.id}>
                        {index > 0 ? " · " : ""}
                        <a
                          className="underline"
                          href={source.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {source.title ?? source.label ?? source.id}
                        </a>
                      </span>
                    ))}
                </p>
              </footer>
            </section>
          </div>
        </ResultsPanel>
      </SidebarLayout>
    </DashboardShell>
  );
}
