"""Validation and SQL storage for CDC PLACES county-level estimates."""
import math
from pathlib import Path

import duckdb

MEASURES = {"DIABETES": "Diabetes", "CASTHMA": "Current asthma", "OBESITY": "Obesity"}
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "oregon.duckdb"


def normalize(rows):
    records = []
    seen = set()
    for row in rows:
        if row.get("stateabbr") != "OR" or row.get("measureid") not in MEASURES:
            continue
        if row.get("data_value_type") != "Crude prevalence":
            continue
        fips = str(row["locationid"]).zfill(5)
        if len(fips) != 5 or not fips.isdigit() or not fips.startswith("41"):
            raise ValueError(f"Invalid Oregon county FIPS: {fips}")
        value = row.get("data_value")
        if value in (None, ""):
            continue  # Suppressed/missing estimates are not zero.
        value = float(value)
        low = float(row["low_confidence_limit"])
        high = float(row["high_confidence_limit"])
        if not all(math.isfinite(x) for x in (value, low, high)) or not 0 <= low <= value <= high <= 100:
            raise ValueError("Invalid prevalence estimate or confidence interval")
        year = int(row["year"])
        key = (fips, row["measureid"], year)
        if key in seen:
            raise ValueError(f"Duplicate county/measure/year: {key}")
        seen.add(key)
        records.append((fips, row["locationname"], row["measureid"], year, value, low, high))
    if not records:
        raise ValueError("No usable Oregon crude-prevalence records found")
    return records


def store(rows, source, path=DB_PATH):
    records = normalize(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as db:
        db.execute("BEGIN")
        db.execute("CREATE OR REPLACE TABLE estimates (fips VARCHAR, county VARCHAR, measure VARCHAR, year INTEGER, prevalence DOUBLE, low DOUBLE, high DOUBLE, PRIMARY KEY (fips, measure, year))")
        db.executemany("INSERT INTO estimates VALUES (?, ?, ?, ?, ?, ?, ?)", records)
        db.execute("CREATE OR REPLACE TABLE provenance (source VARCHAR, imported_at TIMESTAMP)")
        db.execute("INSERT INTO provenance VALUES (?, current_timestamp)", [source])
        db.execute("COMMIT")
    return len(records)


def comparison(db, measure, year):
    return db.execute("""
        SELECT fips, county, prevalence, low, high
        FROM estimates WHERE measure = ? AND year = ?
        ORDER BY prevalence DESC, county
    """, [measure, year]).df()


def demo_rows():
    """Deliberately synthetic fixtures, never a substitute for CDC data."""
    rows = []
    for fips, county, offset in [("41051", "Multnomah", 0), ("41039", "Lane", 1), ("41005", "Clackamas", 2)]:
        for measure, base in [("DIABETES", 10), ("CASTHMA", 12), ("OBESITY", 30)]:
            value = base + offset
            rows.append(dict(stateabbr="OR", locationid=fips, locationname=county,
                             measureid=measure, year="2022", data_value_type="Crude prevalence",
                             data_value=value, low_confidence_limit=value - 1, high_confidence_limit=value + 1))
    return rows
