from copy import deepcopy
import json

import pandas as pd

from oregon_health.data import comparison, demo_rows, store
from oregon_health.map import MISSING, boundaries, county_deck, county_features
import duckdb


def test_boundaries_have_all_oregon_fips_and_valid_geometry():
    features = boundaries()["features"]
    assert len(features) == 36
    ids = {f["properties"]["fips"] for f in features}
    assert len(ids) == 36
    assert all(len(fips) == 5 and fips.startswith("41") for fips in ids)
    assert "41051" in ids
    for feature in features:
        geometry = feature["geometry"]
        assert geometry["type"] in {"Polygon", "MultiPolygon"}
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            for ring in polygon:
                assert ring[0] == ring[-1]
                assert all(-125 < lon < -116 and 41 < lat < 47 for lon, lat in ring)


def test_map_joins_by_fips_and_does_not_mutate_geometry(tmp_path):
    path = tmp_path / "test.duckdb"
    store(demo_rows(), "test", path)
    with duckdb.connect(str(path)) as db:
        frame = comparison(db, "DIABETES", 2022)
    original = deepcopy(boundaries())
    frame.loc[frame.fips == "41051", "county"] = "Different display name"
    collection = county_features(frame, ["Different display name"])
    features = {f["properties"]["fips"]: f["properties"] for f in collection["features"]}
    assert features["41051"]["estimate"] == "10.0%"
    assert features["41051"]["interval"] == "9.0%–11.0%"
    assert features["41051"]["line_width"] == 3
    assert features["41001"]["color"] == MISSING
    assert features["41001"]["estimate"] == "No estimate available"
    assert features["41051"]["color"] != features["41005"]["color"]
    assert boundaries() == original
    spec = json.loads(county_deck(frame, []).to_json())
    assert spec.get("mapProvider") is None
    assert spec["layers"][0]["pickable"]
    assert len(spec["layers"][0]["data"]["features"]) == 36


def test_empty_and_equal_estimates_are_safe():
    empty = pd.DataFrame(columns=["fips", "county", "prevalence", "low", "high"])
    assert all(f["properties"]["color"] == MISSING for f in county_features(empty, [])["features"])
    frame = pd.DataFrame([dict(fips="41051", county="Multnomah", prevalence=10, low=9, high=11)])
    properties = next(f["properties"] for f in county_features(frame, [])["features"] if f["properties"]["fips"] == "41051")
    assert properties["estimate"] == "10.0%"
    assert len(properties["color"]) == 4
