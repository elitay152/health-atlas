"""Data-status UI. Reading this panel never triggers a source request."""
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import streamlit as st

from oregon_health.status import health_report, census_report


def _event(report):
    event = report.get("event")
    if event:
        label = {"success": "Successful fetch", "failed": "Refresh failed", "key_required": "API key required"}[event["outcome"]]
        st.caption(f"Last recorded check: {label} · {event['checked_at']}")
        imported = report.get("imported")
        retained = imported is not None and datetime.fromisoformat(event["checked_at"]) >= imported.replace(tzinfo=timezone.utc)
        if event["outcome"] != "success" and report["loaded"] and retained:
            st.warning("Showing the retained snapshot after an unsuccessful refresh check. Missing cells below describe that snapshot, not the current source service.")
    else:
        st.caption("No refresh outcome has been recorded for this source yet.")


def _snapshot(report, filename):
    st.caption(f"Last successful fetch: {report['imported']} UTC · Cache: {report['freshness']}")
    st.dataframe(report["coverage"], hide_index=True)
    st.caption("Coverage counts usable point estimates. Without uncertainty counts available estimates with no reported interval or margin of error; this is not an additional missing estimate.")
    if not report["missing"].empty:
        st.caption("Missing means no usable estimate in the loaded snapshot; the reason may be suppression, an unreported value, or incomplete source coverage. Missing is not zero.")
        with st.expander("Inspect missing estimates"):
            st.dataframe(report["missing"], hide_index=True)
            st.download_button("Download missing-estimate report", report["missing"].to_csv(index=False), filename, "text/csv", key=f"missing-{filename}")


def render_data_status(path, year, dataset, demo=False):
    st.subheader("Data status & coverage", anchor="data-status")
    with st.expander("Sources, freshness & missing estimates"):
        st.caption("Snapshot status, not a live service monitor. Opening this panel makes no network requests. Refresh windows describe fetch age, not whether the selected release is the newest official release.")
        st.markdown("#### Health estimates")
        try:
            report = health_report(path, year, dataset, demo)
            if report["loaded"]:
                st.caption(f"Source: {report['source']} · Available estimate years: {', '.join(map(str, report['years']))}")
                if demo:
                    st.info("Synthetic demo only. Coverage gaps are absent fixture data, not evidence of missing CDC estimates.")
                elif not report["source_matches"]:
                    st.warning("The retained snapshot is from a different release than the configured CDC dataset.")
                if year is not None:
                    st.caption(f"Coverage for estimate year {year} · Adult crude prevalence · 95% confidence intervals")
                    _snapshot(report, "health-missing-estimates.csv")
            else:
                st.info("No health snapshot is loaded. Coverage has not been evaluated; it is not zero.")
            _event(report)
        except (duckdb.Error, ValueError, OSError):
            st.info("Health snapshot status could not be read. This does not establish that the source service is unavailable.")
        st.markdown("#### Census demographics")
        if demo:
            st.caption("Not used in synthetic demo mode. Real Census snapshots are not mixed into this view.")
        elif year is None:
            st.caption("Select an estimate year after health data loads to inspect Census coverage.")
        else:
            try:
                report = census_report(Path(path).parent, year)
                if report["loaded"]:
                    st.caption(f"Source: [ACS {year-4}–{year} five-year estimates]({report['source']}) · 90% margins of error")
                    _snapshot(report, "census-missing-estimates.csv")
                else:
                    st.info("No Census snapshot is loaded for this year. Open a county profile or include demographics in the comparison to request it. Coverage has not been evaluated.")
                _event(report)
            except (duckdb.Error, ValueError, OSError):
                st.info("Census snapshot status could not be read. Health data remains independent.")
