"""Reproducible county rollout audit; no claims about precise boundary vintage."""
from oregon_health import census, data
from oregon_health.regions import get_region


def audit(region, rows, acs_payload, *, year=2023):
    """Compare both public sources with the configured display catalog by FIPS/name."""
    region = get_region(region)
    expected = region.counties()
    records = data.normalize(rows, region=region)
    demographics = census.normalize(acs_payload, region=region)
    if {record[3] for record in records} != {year}:
        raise ValueError('CDC estimate years differ from the rollout period')
    header = acs_payload[0]
    names = {str(row[header.index('state')]) + str(row[header.index('county')]): row[header.index('NAME')]
             for row in acs_payload[1:]}
    if set(names) != set(expected):
        raise ValueError('Official Census county catalog differs from display boundaries')
    if any(names[code] != f'{name} County, {region.name}' for code, name in expected.items()):
        raise ValueError('Official Census county names differ from display boundaries')
    if any(row['locationname'] != expected[row['locationid']] for row in rows
           if row.get('stateabbr') == region.abbr and row.get('measureid') in data.MEASURES):
        raise ValueError('CDC county names differ from display boundaries')
    coverage = []
    for code, label in data.MEASURES.items():
        available = {record[0] for record in records if record[2] == code}
        coverage.append(dict(measure=code, label=label, available=len(available),
                             expected=len(expected), missing_fips=sorted(set(expected) - available)))
    acs_coverage = []
    for index, label in enumerate(['Population', 'Residents age 65+', 'Below poverty level']):
        available = {record[0] for record in demographics if record[1 + 2 * index] is not None}
        without_moe = [record[0] for record in demographics
                       if record[1 + 2 * index] is not None and record[2 + 2 * index] is None]
        acs_coverage.append(dict(measure=label, available=len(available), expected=len(expected),
                                 missing_fips=sorted(set(expected) - available),
                                 missing_moe_fips=sorted(without_moe)))
    return dict(state=region.abbr, state_fips=region.state_fips,
                county_catalog_join='exact FIPS and name match to Census',
                counties=len(expected), estimate_year=year, acs_period=[year - 4, year],
                cdc_confidence=95, acs_confidence=90, health_coverage=coverage,
                census_coverage=acs_coverage, boundary_vintage='unverified display geometry',
                limitations=['County-level CDC and ACS only in Washington; Oregon-only sources are not evaluated there.',
                             'Missing estimates are unavailable, not zero; county prevalence is not a market-size estimate.',
                             'A matching county ID/name does not establish unchanged boundaries across periods.'])
