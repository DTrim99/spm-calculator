"""CPS ASEC public-use households as in-kind benefit donors.

BLS imputes broadband, LIHEAP, NSLP and WIC participation into the
Consumer Expenditure Survey by pooling CPS ASEC *households* with CE
consumer units (https://www.bls.gov/pir/spmhome.htm, "Imputation of
in-kind benefits at the consumer unit level"). This module turns the
Census public-use files into one harmonized household table per ASEC
survey year so :mod:`spm_calculator.ce_inkind` can fit participation and
amount models on them.

Sources, as published by the Census Bureau:

- survey years 2019 onward: ``asecpubYYcsv.zip`` (``hhpubYY.csv`` and
  ``pppubYY.csv``) from
  ``https://www2.census.gov/programs-surveys/cps/datasets/YYYY/march/``;
- survey years 2015-2018: the fixed-width ``asecYYYY_pubuse*.dat.gz``
  file with its ``*.dd.txt`` / ``*Data_Dict_Full.txt`` dictionary from the
  same directory. The dictionary's ``D NAME SIZE BEGIN`` lines give the
  column positions for each record type (household, family, person).

ASEC survey year ``S`` asks about calendar year ``S - 1`` (the
*reference year*). Every variable used here exists under the same name
in all survey years 2015-2025 except the broadband subsidy
(``HBBSUB_YN``/``HBBSUB_MNTH``, from survey year 2023) and the SPM unit
in-kind values (``SPM_*``, in the public person file from survey year
2019).
"""

from __future__ import annotations

import gzip
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

CPS_DATASETS_URL = "https://www2.census.gov/programs-surveys/cps/datasets"

#: First ASEC survey year Census publishes as a CSV bundle.
FIRST_CSV_SURVEY_YEAR = 2019

#: Fixed-width data and dictionary file names by survey year.
FIXED_WIDTH_FILES = {
    2015: ("asec2015_pubuse.dat.gz", "asec2015early_pubuse.dd.txt"),
    2016: ("asec2016_pubuse_v3.dat.gz", "asec2016_pubuse.dd.txt"),
    2017: ("asec2017_pubuse.dat.gz", "08ASEC2017_Data_Dict_Full.txt"),
    2018: ("asec2018_pubuse.dat.gz", "08ASEC2018_Data_Dict_Full.txt"),
}

HOUSEHOLD_COLUMNS = (
    "H_SEQ",
    "HSUP_WGT",
    "H_NUMPER",
    "GEREG",
    "GESTFIPS",
    "H_TENURE",
    "HPRES_MORT",
    "HTOTVAL",
    "HH5TO18",
    "HFLUNCH",
    "HFLUNNO",
    "HPUBLIC",
    "HLORENT",
    "HFOODSP",
    "HENGAST",
    "HENGVAL",
    "HRWICYN",
    "HRNUMWIC",
)
#: Household columns that only exist in later survey years.
OPTIONAL_HOUSEHOLD_COLUMNS = ("HBBSUB_YN", "HBBSUB_MNTH")

PERSON_COLUMNS = (
    "PH_SEQ",
    "A_EXPRRP",
    "A_AGE",
    "A_SEX",
    "A_HGA",
    "PRDTRACE",
    "PEHSPNON",
    "PEARNVAL",
    "PAW_VAL",
    "SSI_VAL",
)
OPTIONAL_PERSON_COLUMNS = (
    "SPM_ID",
    "SPM_SCHLUNCH",
    "SPM_WICVAL",
    "SPM_ENGVAL",
    "SPM_BBSUBVAL",
)

_DICTIONARY_LINE = re.compile(r"^D\s+(\S+)\s+(\d+)\s+(\d+)")
_RECORD_MARKERS = {"HRECORD": "household", "FRECORD": "family"}


def default_cache_dir() -> Path:
    """Cache directory for CPS ASEC public-use files."""
    import os

    env = os.environ.get("SPM_CALCULATOR_CPS_CACHE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "spm-calculator" / "cps-asec"


def source_files(survey_year: int) -> list[str]:
    """Public-use file names needed for one ASEC survey year."""
    if survey_year >= FIRST_CSV_SURVEY_YEAR:
        return [f"asecpub{survey_year % 100:02d}csv.zip"]
    if survey_year not in FIXED_WIDTH_FILES:
        raise ValueError(
            f"No supported CPS ASEC public-use layout for {survey_year}"
        )
    return list(FIXED_WIDTH_FILES[survey_year])


def source_url(survey_year: int, name: str) -> str:
    return f"{CPS_DATASETS_URL}/{survey_year}/march/{name}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_fixed_width_dictionary(text: str) -> dict[str, dict]:
    """Column layout by record type from a Census ``D`` dictionary.

    Each ``D NAME SIZE BEGIN`` line defines a field. ``HRECORD`` opens
    the household layout and ``FRECORD`` the family layout; every field
    after ``PRECORD`` belongs to the person record. Begin positions are
    1-based in the dictionary and converted to 0-based slices here.
    """
    layouts: dict[str, dict] = {"household": {}, "family": {}, "person": {}}
    current = None
    for raw in text.splitlines():
        match = _DICTIONARY_LINE.match(raw.strip("\r"))
        if not match:
            continue
        name, size, begin = (
            match.group(1),
            int(match.group(2)),
            int(match.group(3)),
        )
        if name in _RECORD_MARKERS:
            current = _RECORD_MARKERS[name]
        elif name == "PRECORD":
            current = "person"
        if current is None:
            raise ValueError(
                f"Dictionary field {name} precedes any record marker"
            )
        if name not in layouts[current]:
            layouts[current][name] = (begin - 1, begin - 1 + size)
    if not all(layouts.values()):
        raise ValueError("Dictionary is missing a record layout")
    return layouts


def _read_fixed_width(
    data_path: Path, layouts: dict[str, dict]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    household_fields = {
        name: layouts["household"][name]
        for name in HOUSEHOLD_COLUMNS
        if name in layouts["household"]
    }
    person_fields = {
        name: layouts["person"][name]
        for name in PERSON_COLUMNS
        if name in layouts["person"]
    }
    missing = [n for n in HOUSEHOLD_COLUMNS if n not in household_fields]
    missing += [n for n in PERSON_COLUMNS if n not in person_fields]
    if missing:
        raise ValueError(f"Fixed-width dictionary lacks columns {missing}")
    households: dict[str, list] = {name: [] for name in household_fields}
    persons: dict[str, list] = {name: [] for name in person_fields}
    with gzip.open(data_path, "rt", encoding="latin-1") as stream:
        for line in stream:
            kind = line[:1]
            if kind == "1":
                for name, (start, end) in household_fields.items():
                    households[name].append(line[start:end])
            elif kind == "3":
                for name, (start, end) in person_fields.items():
                    persons[name].append(line[start:end])
    to_frame = lambda columns: pd.DataFrame(  # noqa: E731
        {
            name: pd.to_numeric(pd.Series(values).str.strip(), errors="coerce")
            for name, values in columns.items()
        }
    )
    return to_frame(households), to_frame(persons)


def _read_csv_bundle(bundle: Path, survey_year: int):
    yy = f"{survey_year % 100:02d}"
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()

        def member(prefix: str) -> str:
            matches = [
                n for n in names if n.lower().endswith(f"{prefix}{yy}.csv")
            ]
            if not matches:
                raise FileNotFoundError(
                    f"{prefix}{yy}.csv not in {bundle.name}"
                )
            return matches[0]

        with archive.open(member("hhpub")) as stream:
            header = pd.read_csv(stream, nrows=0).columns
        household_columns = [
            c
            for c in (*HOUSEHOLD_COLUMNS, *OPTIONAL_HOUSEHOLD_COLUMNS)
            if c in header
        ]
        with archive.open(member("hhpub")) as stream:
            households = pd.read_csv(stream, usecols=household_columns)
        with archive.open(member("pppub")) as stream:
            header = pd.read_csv(stream, nrows=0).columns
        person_columns = [
            c
            for c in (*PERSON_COLUMNS, *OPTIONAL_PERSON_COLUMNS)
            if c in header
        ]
        with archive.open(member("pppub")) as stream:
            persons = pd.read_csv(stream, usecols=person_columns)
    missing = [c for c in HOUSEHOLD_COLUMNS if c not in households]
    missing += [c for c in PERSON_COLUMNS if c not in persons]
    if missing:
        raise ValueError(f"{bundle.name} lacks required columns {missing}")
    return households, persons


def load_raw_asec(
    survey_year: int, cache_dir: Optional[Path] = None
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Household and person records with source identity.

    Raises ``FileNotFoundError`` when a required cached file is absent;
    this loader never downloads.
    """
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    files = [cache / name for name in source_files(survey_year)]
    absent = [str(path) for path in files if not path.exists()]
    if absent:
        raise FileNotFoundError(
            f"CPS ASEC {survey_year} public-use files not cached: {absent}. "
            f"Download them from {CPS_DATASETS_URL}/{survey_year}/march/"
        )
    if survey_year >= FIRST_CSV_SURVEY_YEAR:
        households, persons = _read_csv_bundle(files[0], survey_year)
    else:
        layouts = parse_fixed_width_dictionary(
            files[1].read_text(encoding="latin-1")
        )
        households, persons = _read_fixed_width(files[0], layouts)
    provenance = {
        "survey_year": survey_year,
        "reference_year": survey_year - 1,
        "files": {
            path.name: {
                "url": source_url(survey_year, path.name),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in files
        },
    }
    return households, persons, provenance


def _education_group(hga: pd.Series) -> pd.Series:
    """Four education groups from ``A_HGA`` (31-46 coding)."""
    codes = pd.to_numeric(hga, errors="coerce")
    return pd.Series(
        np.select(
            [codes < 39, codes == 39, codes.between(40, 42), codes >= 43],
            ["lt_hs", "hs", "some_college", "ba_plus"],
            default="lt_hs",
        ),
        index=hga.index,
    )


def _race_group(code: pd.Series) -> pd.Series:
    values = pd.to_numeric(code, errors="coerce")
    return pd.Series(
        np.select([values == 1, values == 2], ["white", "black"], "other"),
        index=code.index,
    )


def build_household_donors(
    households: pd.DataFrame, persons: pd.DataFrame, survey_year: int
) -> pd.DataFrame:
    """Harmonize interviewed CPS ASEC households into donor records.

    Returns one row per interviewed household (``H_NUMPER > 0``) with
    the covariates shared with CE consumer units (see
    :func:`spm_calculator.ce_inkind.ce_unit_covariates`) and the
    program outcomes:

    - ``nslp``/``nslp_children``: ``HFLUNCH == 1`` and ``HFLUNNO``;
    - ``wic``/``wic_people``: ``HRWICYN == 1`` and ``HRNUMWIC``;
    - ``liheap``/``liheap_amount``: ``HENGAST == 1`` and ``HENGVAL``;
    - ``broadband``/``broadband_months``: ``HBBSUB_YN == 1`` and
      ``HBBSUB_MNTH`` (survey years 2023 onward; missing before);
    - ``spm_*``: the Census SPM unit values summed over the household's
      SPM units (survey years 2019 onward; missing before).
    """
    hh = households.copy()
    hh = hh.loc[pd.to_numeric(hh["H_NUMPER"], errors="coerce") > 0].copy()
    pp = persons.copy()
    for column in pp.columns:
        pp[column] = pd.to_numeric(pp[column], errors="coerce")
    age = pp["A_AGE"]
    female = pp["A_SEX"] == 2
    pp["lt5"] = age < 5
    pp["a5_18"] = (age >= 5) & (age <= 18)
    pp["lt18"] = age < 18
    pp["ge65"] = age >= 65
    pp["woman_15_45"] = female & (age >= 15) & (age <= 45)
    pp["woman_15_plus"] = female & (age >= 15)
    pp["earner"] = pp["PEARNVAL"].fillna(0) > 0
    pp["public_assistance"] = pp["PAW_VAL"].fillna(0) > 0
    pp["ssi"] = pp["SSI_VAL"].fillna(0) > 0
    counts = pp.groupby("PH_SEQ").agg(
        n_people=("A_AGE", "size"),
        n_lt5=("lt5", "sum"),
        n_5_18=("a5_18", "sum"),
        n_lt18=("lt18", "sum"),
        n_ge65=("ge65", "sum"),
        n_women_15_45=("woman_15_45", "sum"),
        n_women_15_plus=("woman_15_plus", "sum"),
        n_earners=("earner", "sum"),
        public_assistance=("public_assistance", "any"),
        ssi=("ssi", "any"),
    )
    heads = pp.loc[pp["A_EXPRRP"].isin([1, 2])].drop_duplicates("PH_SEQ")
    head = pd.DataFrame(
        {
            "head_age": heads["A_AGE"].to_numpy(),
            "head_female": (heads["A_SEX"] == 2).to_numpy(),
            "head_race": _race_group(heads["PRDTRACE"]).to_numpy(),
            "head_hispanic": (heads["PEHSPNON"] == 1).to_numpy(),
            "head_education": _education_group(heads["A_HGA"]).to_numpy(),
        },
        index=heads["PH_SEQ"].to_numpy(),
    )
    spm_columns = [
        c for c in OPTIONAL_PERSON_COLUMNS if c in pp and c != "SPM_ID"
    ]
    if "SPM_ID" in pp and spm_columns:
        units = pp.drop_duplicates("SPM_ID")[["PH_SEQ", *spm_columns]]
        spm = units.groupby("PH_SEQ")[spm_columns].sum()
        spm.columns = [column.lower() for column in spm.columns]
    else:
        spm = pd.DataFrame(index=pd.Index([], name="PH_SEQ"))
    out = (
        hh.set_index("H_SEQ").join(counts, how="inner").join(head, how="inner")
    )
    out = out.join(spm, how="left")
    tenure = pd.to_numeric(out["H_TENURE"], errors="coerce")
    mortgage = pd.to_numeric(out["HPRES_MORT"], errors="coerce")
    donors = pd.DataFrame(
        {
            "survey_year": survey_year,
            "reference_year": survey_year - 1,
            "household_id": out.index.to_numpy(),
            "weight": pd.to_numeric(out["HSUP_WGT"], errors="coerce") / 100,
            "region": pd.to_numeric(out["GEREG"], errors="coerce"),
            "state": pd.to_numeric(out["GESTFIPS"], errors="coerce"),
            "tenure": np.select(
                [
                    (tenure == 1) & (mortgage == 1),
                    (tenure == 1) & (mortgage != 1),
                    tenure == 2,
                ],
                ["owner_with_mortgage", "owner_without_mortgage", "renter"],
                default="no_cash_rent",
            ),
            "income": pd.to_numeric(out["HTOTVAL"], errors="coerce"),
            "snap": pd.to_numeric(out["HFOODSP"], errors="coerce") == 1,
            "housing_assistance": (
                pd.to_numeric(out["HPUBLIC"], errors="coerce") == 1
            )
            | (pd.to_numeric(out["HLORENT"], errors="coerce") == 1),
            "nslp": pd.to_numeric(out["HFLUNCH"], errors="coerce") == 1,
            "nslp_children": pd.to_numeric(
                out["HFLUNNO"], errors="coerce"
            ).fillna(0),
            "wic": pd.to_numeric(out["HRWICYN"], errors="coerce") == 1,
            "wic_people": pd.to_numeric(out["HRNUMWIC"], errors="coerce")
            .fillna(0)
            .clip(lower=0),
            "liheap": pd.to_numeric(out["HENGAST"], errors="coerce") == 1,
            "liheap_amount": pd.to_numeric(out["HENGVAL"], errors="coerce")
            .fillna(0)
            .clip(lower=0),
        }
    )
    for column in (
        "n_people",
        "n_lt5",
        "n_5_18",
        "n_lt18",
        "n_ge65",
        "n_women_15_45",
        "n_women_15_plus",
        "n_earners",
    ):
        donors[column] = out[column].astype(int).to_numpy()
    for column in ("public_assistance", "ssi"):
        donors[column] = out[column].astype(bool).to_numpy()
    for column in head.columns:
        donors[column] = out[column].to_numpy()
    if "HBBSUB_YN" in out:
        donors["broadband"] = (
            pd.to_numeric(out["HBBSUB_YN"], errors="coerce") == 1
        ).to_numpy()
        donors["broadband_months"] = (
            pd.to_numeric(out["HBBSUB_MNTH"], errors="coerce")
            .fillna(0)
            .clip(0, 12)
            .to_numpy()
        )
    else:
        donors["broadband"] = np.nan
        donors["broadband_months"] = np.nan
    for column in ("spm_schlunch", "spm_wicval", "spm_engval", "spm_bbsubval"):
        donors[column] = (
            out[column].to_numpy()
            if column in out
            else np.full(len(out), np.nan)
        )
    donors = donors.reset_index(drop=True)
    if not (np.isfinite(donors["weight"]) & (donors["weight"] >= 0)).all():
        raise ValueError("CPS ASEC household weights must be finite")
    donors = donors.loc[donors["weight"] > 0].reset_index(drop=True)
    return donors


def load_household_donors(
    survey_years: Iterable[int], cache_dir: Optional[Path] = None
) -> tuple[pd.DataFrame, dict]:
    """Donor households for several survey years with provenance."""
    frames, provenance = [], {}
    for survey_year in sorted(set(survey_years)):
        households, persons, source = load_raw_asec(survey_year, cache_dir)
        frames.append(build_household_donors(households, persons, survey_year))
        provenance[str(survey_year)] = source
    return pd.concat(frames, ignore_index=True), provenance
