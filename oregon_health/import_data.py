"""Run: python -m oregon_health.import_data --dataset COUNTY_DATASET_ID"""
import argparse
import re

import requests

from oregon_health.data import MEASURES, store
from oregon_health.regions import OREGON, get_region


def fetch(dataset, *, region=OREGON):
    region=get_region(region)
    region.require("county_health")
    if not re.fullmatch(r"[a-z0-9]{4}-[a-z0-9]{4}", dataset):
        raise ValueError("Expected a CDC Socrata dataset ID such as abcd-1234")
    base = "https://data.cdc.gov"
    response = requests.get(f"{base}/api/views/{dataset}.json", timeout=(5, 30))
    response.raise_for_status()
    title = response.json()["name"]
    if "places" not in title.lower() or "county" not in title.lower():
        raise ValueError(f"Expected a PLACES county dataset, received: {title}")
    measure_ids = ", ".join(f"'{code}'" for code in MEASURES)
    response = requests.get(f"{base}/resource/{dataset}.json", params={
        "$where": f"stateabbr = '{region.abbr}' AND measureid IN ({measure_ids}) AND data_value_type = 'Crude prevalence'",
        "$limit": 1000, "$offset": 0, "$order": ":id",
    }, timeout=(5, 30))
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("CDC returned an unexpected response")
    if len(rows) >= 1000:
        raise ValueError("CDC response exceeds the 1,000-record limit for this county explorer")
    return rows, f"{title} | {base}/d/{dataset}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="ID of a CDC PLACES county release, GIS-friendly format")
    parser.add_argument("--region", choices=["OR","WA"], default="OR")
    args = parser.parse_args()
    region=get_region(args.region)
    rows, source = fetch(args.dataset,region=region)
    count = store(rows, source,region=region)
    print(f"Imported {count} estimates from {source}")


if __name__ == "__main__":
    main()
