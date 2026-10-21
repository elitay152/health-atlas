"""Run: python -m oregon_health.import_data --dataset COUNTY_DATASET_ID"""
import argparse
import re

import requests

from oregon_health.data import store


def fetch(dataset):
    if not re.fullmatch(r"[a-z0-9]{4}-[a-z0-9]{4}", dataset):
        raise ValueError("Expected a CDC Socrata dataset ID such as abcd-1234")
    base = "https://data.cdc.gov"
    response = requests.get(f"{base}/api/views/{dataset}.json", timeout=60)
    response.raise_for_status()
    title = response.json()["name"]
    if "places" not in title.lower() or "county" not in title.lower():
        raise ValueError(f"Expected a PLACES county dataset, received: {title}")
    rows = []
    offset = 0
    while True:
        response = requests.get(f"{base}/resource/{dataset}.json", params={
            "$where": "stateabbr = 'OR' AND measureid IN ('DIABETES', 'CASTHMA', 'OBESITY') AND data_value_type = 'Crude prevalence'",
            "$limit": 1000, "$offset": offset, "$order": ":id",
        }, timeout=60)
        response.raise_for_status()
        batch = response.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += len(batch)
    return rows, f"{title} | {base}/d/{dataset}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="ID of a CDC PLACES county release, GIS-friendly format")
    args = parser.parse_args()
    rows, source = fetch(args.dataset)
    count = store(rows, source)
    print(f"Imported {count} estimates from {source}")


if __name__ == "__main__":
    main()
