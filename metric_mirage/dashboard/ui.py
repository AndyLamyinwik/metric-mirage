"""Interactive local research workspace backed by the existing Python APIs."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from typing import Any, Callable

import plotly.graph_objects as go
import streamlit as st

from ..drift_report import DriftReport, render_json, render_markdown
from ..reports import REPORT_TYPES, ReportRecord
from .services import LoadedDefinition, Workspace, demo_analysis


PAGES = (
    "Overview", "Report Manager", "Financial Metric Explorer",
    "Drift Analysis", "Evidence Explorer",
)
_EXPECTED_ERRORS = (OSError, ValueError, TypeError)
_COLORS = ("#79DFC3", "#F2A697", "#8BACC9")
_PERSISTENT_INPUTS = (
    "ticker", "company", "report_type", "fiscal_period", "source_name",
    "explorer_company", "explorer_period", "explorer_type", "keyword",
    "search_max_results", "selected_adjustments", "old_definition", "new_definition",
    "evidence_definition", "evidence_report_company", "evidence_report_period",
    "evidence_report_type", "evidence_adjustment", "overview_adjustment", "drift_adjustment",
    "include_evidence",
)
_CSS = """
<style>
.block-container {max-width: 1380px; padding-top: 2.1rem; padding-bottom: 3rem;}
[data-testid="stSidebar"] {border-right: 1px solid #2b3a4b;}
[data-testid="stSidebar"] .block-container {padding-top: 1.5rem;}
h1 {letter-spacing: -.045em; font-weight: 750;}
h2, h3 {letter-spacing: -.025em;}
[data-testid="stMetric"] {background: #182434; border: 1px solid #2b3a4b;
    border-radius: 12px; padding: 1.05rem 1.2rem;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
[data-testid="stCaptionContainer"] {color: #A4B7CA;}
[data-testid="stForm"] {border-color: #2b3a4b; border-radius: 12px;}
@media (max-width: 640px) {.block-container {padding-left: 1rem; padding-right: 1rem;}}
</style>
"""


def _attempt(action: Callable[[], Any], *, failure_key: str | None = None) -> Any:
    """Present expected user input failures without an uncaught traceback."""
    try:
        return action()
    except _EXPECTED_ERRORS as error:
        if failure_key:
            st.session_state[failure_key] = str(error)
        else:
            st.error(str(error))
        return None


def _sync_context(key: str, fingerprint: object, clear: tuple[str, ...], *,
                  preserve_initial: bool = False) -> None:
    if key not in st.session_state and preserve_initial:
        st.session_state[key] = fingerprint
        return
    if st.session_state.get(key) != fingerprint:
        for name in clear:
            st.session_state.pop(name, None)
        st.session_state[key] = fingerprint


def _remember_upload(widget_key: str, data_key: str) -> None:
    """Store JSON bytes outside widget state, which Streamlit removes off-page."""
    upload = st.session_state.get(widget_key)
    st.session_state[data_key] = None if upload is None else upload.getvalue()


def _upload_key(base: str) -> str:
    generation = st.session_state.get(f"{base}_generation", 0)
    return base if generation == 0 else f"{base}_{generation}"


def _reset_upload(base: str, data_key: str) -> None:
    st.session_state[data_key] = None
    st.session_state[f"{base}_generation"] = st.session_state.get(f"{base}_generation", 0) + 1


def _report_identity(report: ReportRecord) -> tuple[str, ...]:
    return (report.ticker, report.company, report.fiscal_period, report.report_type,
            report.file_sha256, report.local_file)


def _definition_identity(definition: LoadedDefinition | None) -> object:
    return None if definition is None else (str(definition.path), definition.sha256)


def _definition_label(definition: LoadedDefinition) -> str:
    value = definition.definition
    return f"{value.company} · {value.fiscal_period} · {value.metric_name} · {definition.sha256[:8]}"


def _comparison_rows(report: DriftReport) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for names, old, new, status in (
        (report["added_adjustments"], "No", "Yes", "ADDED"),
        (report["removed_adjustments"], "Yes", "No", "REMOVED"),
        (report["unchanged_adjustments"], "Yes", "Yes", "UNCHANGED"),
    ):
        rows.extend({"Adjustment": name, "Old Period": old, "New Period": new,
                     "Status": status} for name in names)
    return sorted(rows, key=lambda row: row["Adjustment"])


def _counts(report: DriftReport, *, chart_key: str) -> None:
    labels = ("Added Adjustments", "Removed Adjustments", "Unchanged Adjustments")
    values = [len(report[name]) for name in (
        "added_adjustments", "removed_adjustments", "unchanged_adjustments",
    )]
    for column, label, value in zip(st.columns(3), labels, values):
        column.metric(label, value)
    figure = go.Figure(go.Bar(
        x=["Added", "Removed", "Unchanged"], y=values,
        marker_color=list(_COLORS), text=values, textposition="outside",
        hovertemplate="%{x}: %{y} adjustments<extra></extra>",
    ))
    figure.update_layout(
        height=280, margin=dict(l=10, r=15, t=25, b=15),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#C9D8E6", family="Arial, sans-serif"),
        xaxis=dict(title=None, fixedrange=True),
        yaxis=dict(title="Adjustment count", dtick=1, rangemode="tozero",
                   range=[0, max(values + [1]) * 1.25], gridcolor="#2B3A4B", fixedrange=True),
        showlegend=False,
    )
    st.plotly_chart(figure, width="stretch", key=chart_key,
                    config={"displayModeBar": False})


def _summary(report: DriftReport) -> None:
    st.subheader("Definition Drift Summary")
    if report["has_definition_drift"]:
        st.success("Definition Set Changed")
    else:
        st.info("No Set Changes Detected")
    columns = st.columns(4)
    for column, label, value in zip(columns,
                                   ("Company", "Metric Name", "Old Period", "New Period"),
                                   (report["company"], report["metric_name"],
                                    report["old_period"], report["new_period"])):
        column.caption(label)
        column.write(value)
    st.caption(f"has_definition_drift = {str(report['has_definition_drift']).lower()}")


def _sources(report: DriftReport) -> None:
    with st.expander("Document sources and recorded SHA256"):
        for column, label, value in zip(st.columns(2), ("Old Source", "New Source"),
                                        (report["old_source"], report["new_source"])):
            column.caption(label)
            column.code(value, language=None, wrap_lines=True)


def _download_analysis(report: DriftReport) -> None:
    stem = f"metric_mirage_{report['old_period']}_{report['new_period']}"
    left, right = st.columns(2)
    left.download_button("Download Markdown", render_markdown(report).encode("utf-8"),
                         file_name=f"{stem}.md", mime="text/markdown", key="download_markdown",
                         width="stretch")
    right.download_button("Download JSON", render_json(report).encode("utf-8"),
                          file_name=f"{stem}.json", mime="application/json", key="download_json",
                          width="stretch")


def _analysis_evidence(report: DriftReport, *, key: str) -> None:
    st.subheader("Adjustment Evidence")
    if "evidence" not in report:
        st.info("No Evidence Attached")
        st.caption("No PDF pages or quotations are generated for an analysis without snapshots.")
        return
    st.success("Evidence Available")
    items = report["evidence"]
    if not items:
        st.info("Both user-confirmed adjustment sets are empty.")
        return
    names = [item["canonical_name"] for item in items]
    name = st.selectbox("Inspect adjustment", names, key=key)
    item = next(value for value in items if value["canonical_name"] == name)
    for column, period, entries in zip(st.columns(2), (report["old_period"], report["new_period"]),
                                      (item["old_evidence"], item["new_evidence"])):
        with column:
            st.markdown(f"**{period}**")
            if not entries:
                st.caption("Not in this user-confirmed set; no evidence attached for this side.")
            for entry in entries:
                st.caption(f"PDF Page {entry['page_number']} · Matched Text")
                st.code(entry["matched_text"], language=None, wrap_lines=True)
                st.code(entry["evidence_excerpt"], language=None, wrap_lines=True)
                st.caption("Report SHA256")
                st.code(entry["report_sha256"], language=None, wrap_lines=True)
    st.caption("Snapshot identifiers and recorded associations do not authenticate a source "
               "or verify the PDF's current contents.")


def _overview(workspace: Workspace) -> None:
    st.subheader("A clear view of changing definitions")
    st.write("Trace how user-confirmed financial adjustment sets change across reporting periods. "
             "Register local Hong Kong financial reports, inspect original PDF text, and keep "
             "the evidence beside your comparison.")
    if st.button("Explore Demo", type="primary", key="explore_demo"):
        report = _attempt(demo_analysis)
        if report is not None:
            st.session_state["analysis"] = report
            st.session_state["analysis_ticker"] = None
            st.session_state["analysis_synthetic"] = True
    report = st.session_state.get("analysis")
    st.divider()
    st.subheader("Current Analysis")
    if report is None:
        st.info("No analysis yet. Explore the SYNTHETIC demo or compare your confirmed definitions.")
        st.caption("The demo runs the same comparison engine and requires no report downloads.")
        return
    if st.session_state.get("analysis_synthetic"):
        st.warning("SYNTHETIC DEMO · Invented company and adjustment definitions; no real company analysis.")
    st.caption(f"Stock Ticker: {st.session_state.get('analysis_ticker') or 'Not supplied'} · "
               f"Fiscal Period: {report['old_period']} → {report['new_period']}")
    _summary(report)
    _counts(report, chart_key="overview_counts")
    _analysis_evidence(report, key="overview_adjustment")


def _report_manager(workspace: Workspace) -> None:
    st.subheader("Register a local financial report")
    st.caption("Upload a PDF you have permission to process. Files remain in the local ignored "
               "workspace; no network download is performed. Maximum PDF size: 25 MiB.")
    with st.form("register_report_form", clear_on_submit=False):
        upload = st.file_uploader("Financial report PDF", type=["pdf"], key="pdf_upload",
                                  max_upload_size=25)
        left, right = st.columns(2)
        ticker = left.text_input("Hong Kong Stock Code", placeholder="0700 or 00700", key="ticker")
        company = right.text_input("Company", placeholder="Company name", key="company")
        left, right = st.columns(2)
        report_type = left.selectbox("Report Type", REPORT_TYPES, key="report_type")
        period = right.text_input("Fiscal Period", placeholder="FY2024", key="fiscal_period")
        source = st.text_input("Source Description", placeholder="Issuer IR; supplied by user", key="source_name")
        submitted = st.form_submit_button("Register PDF", type="primary", key="register_pdf")
    if submitted:
        if upload is None:
            st.error("Choose a PDF before registering the report.")
        else:
            record = _attempt(lambda: workspace.register_pdf(
                upload.getvalue(), filename=upload.name, ticker=ticker, company=company,
                report_type=report_type, fiscal_period=period, source_name=source,
            ))
            if record is not None:
                st.session_state["registered_report"] = record
                st.success(f"Report registered: {record.company} · {record.ticker} · {record.fiscal_period}")
    st.divider()
    st.subheader("Registered Reports")
    reports = _attempt(workspace.reports)
    if reports is None:
        return
    if not reports:
        st.info("No reports registered yet. Add a local PDF above to begin.")
        return
    st.dataframe([{"Ticker": report.ticker, "Company": report.company,
                   "Fiscal Period": report.fiscal_period, "Report Type": report.report_type,
                   "Source": report.source_name, "PDF SHA256": report.file_sha256}
                  for report in reports], hide_index=True, width="stretch")
    with st.expander("Stored file paths and content identifiers"):
        st.dataframe([asdict(report) for report in reports], hide_index=True, width="stretch")


def _choose_report(reports: list[ReportRecord], *, prefix: str) -> ReportRecord | None:
    if not reports:
        st.info("No reports registered. Register a PDF in Report Manager first.")
        return None
    companies = sorted({(report.ticker, report.company) for report in reports})
    labels = {f"{company} · {ticker}": (ticker, company) for ticker, company in companies}
    company_label = st.selectbox("Company / Stock Ticker", list(labels), key=f"{prefix}_company")
    ticker, company = labels[company_label]
    company_reports = [r for r in reports if r.ticker == ticker and r.company == company]
    left, right = st.columns(2)
    period = left.selectbox("Fiscal Period", sorted({r.fiscal_period for r in company_reports}),
                            key=f"{prefix}_period")
    period_reports = [r for r in company_reports if r.fiscal_period == period]
    report_type = right.selectbox("Report Type", sorted({r.report_type for r in period_reports}),
                                  key=f"{prefix}_type")
    matches = [r for r in period_reports if r.report_type == report_type]
    if len(matches) != 1:
        st.error("Multiple registered reports match this company, fiscal period and report type. "
                 "Review Report Manager; a report cannot be chosen unambiguously.")
        return None
    return matches[0]


def _metric_explorer(workspace: Workspace) -> None:
    st.subheader("Explore the report text")
    reports = _attempt(workspace.reports)
    if reports is None:
        return
    report = _choose_report(reports, prefix="explorer")
    _sync_context("explorer_context", None if report is None else _report_identity(report),
                  ("search_result", "candidate_result", "confirmed_definition", "selected_adjustments"))
    if report is None:
        return
    st.caption(f"{report.source_name} · PDF pages use one-based file positions, not printed page labels.")
    with st.container(border=True):
        st.markdown("**Literal Keyword Search**")
        if "keyword" not in st.session_state:
            st.session_state["keyword"] = "經調整淨利潤"
        keyword = st.text_input("Keyword", key="keyword")
        if "search_max_results" not in st.session_state:
            st.session_state["search_max_results"] = 10
        limit = st.number_input("Maximum Results", min_value=1, max_value=1000,
                                step=1, key="search_max_results")
        _sync_context("search_context", (_report_identity(report), keyword, limit), ("search_result",))
        if st.button("Search", type="primary", key="search"):
            st.session_state.pop("search_result", None)
            result = _attempt(lambda: workspace.search(report, keyword, max_results=int(limit)))
            if result is not None:
                st.session_state["search_result"] = result
        result = st.session_state.get("search_result")
        if result is not None:
            st.caption(f"{result.total_matches} total matches · {len(result.matches)} shown")
            if not result.matches:
                st.info("No keyword matches found. The PDF contains extractable text.")
            for index, match in enumerate(result.matches, 1):
                with st.expander(f"PDF Page {match.page_number} · {match.keyword} · Match {index}", expanded=True):
                    st.code(match.excerpt, language=None, wrap_lines=True)
    st.divider()
    st.subheader("Adjustment Candidates")
    st.caption("Conservative rules currently support Adjusted Net Profit. Candidates are original "
               "text associations, not an independently verified complete formula.")
    if st.button("Extract Candidates", key="extract_candidates"):
        for name in ("candidate_result", "confirmed_definition", "selected_adjustments"):
            st.session_state.pop(name, None)
        result = _attempt(lambda: workspace.candidates(report))
        if result is not None:
            st.session_state["candidate_result"] = result
    candidates = st.session_state.get("candidate_result")
    if candidates is None:
        return
    if not candidates.candidates:
        st.info("No supported adjustment candidates found with sufficient contextual evidence.")
        return
    st.dataframe([{"canonical_name": candidate.canonical_name,
                   "matched_text": candidate.matched_text,
                   "page_number": candidate.page_number,
                   "evidence_excerpt": candidate.evidence_excerpt}
                  for candidate in candidates.candidates], hide_index=True, width="stretch")
    for candidate in candidates.candidates:
        with st.expander(f"{candidate.canonical_name} · PDF Page {candidate.page_number}"):
            st.caption("Matched Text")
            st.code(candidate.matched_text, language=None, wrap_lines=True)
            st.caption("Original Evidence Excerpt")
            st.code(candidate.evidence_excerpt, language=None, wrap_lines=True)
    if "selected_adjustments" not in st.session_state:
        st.session_state["selected_adjustments"] = []
    selected = st.multiselect("Select adjustments to confirm", sorted({
        candidate.canonical_name for candidate in candidates.candidates
    }), key="selected_adjustments")
    _sync_context("confirmation_context", (_report_identity(report), tuple(sorted(selected))),
                  ("confirmed_definition",))
    if st.button("Confirm Selection", type="primary", key="confirm_selection"):
        confirmed = _attempt(lambda: workspace.confirm(report, selected))
        if confirmed is not None:
            st.session_state["confirmed_definition"] = confirmed
            st.success("Selected candidates exported as a MetricDefinition.")
    confirmed = st.session_state.get("confirmed_definition")
    if confirmed is not None:
        st.caption("Only your explicit selections are included. Confirmation does not independently "
                   "verify the complete financial formula.")
        raw = _attempt(lambda: workspace.read_artifact(confirmed.path))
        if raw is not None:
            st.download_button("Download MetricDefinition JSON", raw,
                               file_name=f"definition_{confirmed.definition.fiscal_period}.json",
                               mime="application/json", key="download_definition")
        st.caption(f"Definition SHA256: {confirmed.sha256}")


def _saved_definitions(workspace: Workspace) -> list[LoadedDefinition] | None:
    return _attempt(workspace.definitions)


def _uploaded_definition(workspace: Workspace, data: bytes | None, side: str) -> LoadedDefinition | None:
    identity = None if data is None else hashlib.sha256(data).hexdigest()
    _sync_context(f"{side}_upload_context", identity,
                  (f"{side}_uploaded_definition", f"{side}_definition_error"))
    if data is None:
        return None
    existing = st.session_state.get(f"{side}_uploaded_definition")
    if existing is not None:
        return existing
    error = st.session_state.get(f"{side}_definition_error")
    if error:
        st.error(f"Definition Validation Failed: {error}")
        return None
    loaded = _attempt(lambda: workspace.import_definition(data),
                      failure_key=f"{side}_definition_error")
    if loaded is not None:
        st.session_state[f"{side}_uploaded_definition"] = loaded
    else:
        st.error(f"Definition Validation Failed: {st.session_state[f'{side}_definition_error']}")
    return loaded


def _pick_definition(workspace: Workspace, definitions: list[LoadedDefinition], *, side: str) -> LoadedDefinition | None:
    st.markdown(f"**{'Old' if side == 'old' else 'New'} Year Definition**")
    labels = {_definition_label(item): item for item in definitions}
    selected = st.selectbox("Saved MetricDefinition", list(labels), index=None,
                            placeholder="Select a confirmed definition", key=f"{side}_definition")
    base_key, data_key = f"{side}_definition_upload", f"{side}_definition_upload_data"
    widget_key = _upload_key(base_key)
    st.file_uploader("Or upload MetricDefinition JSON", type=["json"], key=widget_key,
                     on_change=_remember_upload, args=(widget_key, data_key), max_upload_size=2)
    data = st.session_state.get(data_key)
    loaded = _uploaded_definition(workspace, data, side)
    if data is not None:
        st.caption("Uploaded JSON takes precedence over the saved selection.")
        if loaded is not None:
            st.caption(_definition_label(loaded))
        if st.button("Remove Uploaded Definition", key=f"{side}_clear_definition"):
            _reset_upload(base_key, data_key)
            st.session_state.pop(f"{side}_uploaded_definition", None)
            st.rerun()
        return loaded
    return labels.get(selected)


def _pick_evidence(workspace: Workspace, definition: LoadedDefinition | None, *, side: str) -> dict[str, Any] | None:
    base_key, data_key = f"{side}_evidence_upload", f"{side}_evidence_upload_data"
    widget_key = _upload_key(base_key)
    st.file_uploader("Optional Evidence Snapshot JSON", type=["json"], key=widget_key,
                     on_change=_remember_upload, args=(widget_key, data_key), max_upload_size=2)
    data = st.session_state.get(data_key)
    digest = None if data is None else hashlib.sha256(data).hexdigest()
    fingerprint = (_definition_identity(definition), digest)
    _sync_context(f"{side}_evidence_context", fingerprint, (f"{side}_evidence", f"{side}_evidence_error"))
    if definition is None:
        if data is not None:
            st.info("Choose the matching definition before validating this snapshot.")
        return None
    snapshots = st.session_state.setdefault("evidence_snapshots", {})
    if data is not None and st.session_state.get(f"{side}_evidence") is None and not st.session_state.get(f"{side}_evidence_error"):
        validated = _attempt(lambda: workspace.import_evidence(data, definition),
                              failure_key=f"{side}_evidence_error")
        if validated is not None:
            snapshot, path = validated
            st.session_state[f"{side}_evidence"] = snapshot
            snapshots[definition.sha256] = (snapshot, path)
    elif data is None and definition.sha256 in snapshots:
        st.session_state[f"{side}_evidence"] = snapshots[definition.sha256][0]
    if data is not None and st.button("Remove Uploaded Evidence", key=f"{side}_clear_evidence"):
        _reset_upload(base_key, data_key)
        st.rerun()
    error = st.session_state.get(f"{side}_evidence_error")
    if error:
        st.error(f"Evidence Validation Failed: {error}")
        return None
    snapshot = st.session_state.get(f"{side}_evidence")
    if snapshot is None:
        st.caption("No Evidence Attached")
    else:
        st.caption("Evidence Available")
    return snapshot


def _drift_analysis(workspace: Workspace) -> None:
    st.subheader("Compare confirmed adjustment sets")
    st.caption("Choose or upload two MetricDefinition files. Periods must use FY followed by four "
               "digits, with the new year later than the old year. JSON uploads: maximum 2 MiB.")
    definitions = _saved_definitions(workspace)
    if definitions is None:
        return
    left, right = st.columns(2)
    with left:
        old = _pick_definition(workspace, definitions, side="old")
    with right:
        new = _pick_definition(workspace, definitions, side="new")
    upload_identities = tuple(
        None if st.session_state.get(f"{side}_definition_upload_data") is None else
        hashlib.sha256(st.session_state[f"{side}_definition_upload_data"]).hexdigest()
        for side in ("old", "new")
    )
    _sync_context("drift_context", (_definition_identity(old), _definition_identity(new), upload_identities),
                  ("analysis", "analysis_ticker", "analysis_synthetic"),
                  preserve_initial=old is None and new is None and upload_identities == (None, None))
    with left:
        old_evidence = _pick_evidence(workspace, old, side="old")
    with right:
        new_evidence = _pick_evidence(workspace, new, side="new")
    evidence_fingerprint = tuple((
        None if snapshot is None else (snapshot["definition_sha256"], snapshot["report_sha256"],
                                       str(snapshot["items"])),
        None if st.session_state.get(f"{side}_evidence_upload_data") is None else
        hashlib.sha256(st.session_state[f"{side}_evidence_upload_data"]).hexdigest(),
        st.session_state.get(f"{side}_evidence_error"),
    ) for side, snapshot in (("old", old_evidence), ("new", new_evidence)))
    if "include_evidence" not in st.session_state:
        st.session_state["include_evidence"] = True
    include_evidence = st.checkbox("Include Evidence Snapshots", key="include_evidence",
                                   help="Attach both validated snapshots, or turn this off to compare without evidence.")
    _sync_context("drift_evidence_context", (evidence_fingerprint, include_evidence),
                  ("analysis", "analysis_ticker", "analysis_synthetic"),
                  preserve_initial=not any(any(value) for value in evidence_fingerprint))
    invalid_evidence = bool(st.session_state.get("old_evidence_error") or
                            st.session_state.get("new_evidence_error"))
    if st.button("Run Drift Analysis", type="primary", key="run_analysis"):
        st.session_state.pop("analysis", None)
        if old is None or new is None:
            st.error("Choose or upload both old and new MetricDefinition files.")
        elif invalid_evidence:
            st.error("Evidence Validation Failed. Replace or remove the invalid snapshot before analysing.")
        elif include_evidence and (old_evidence is None) != (new_evidence is None):
            st.error("Attach matching evidence for both years, or turn off Include Evidence Snapshots "
                     "to compare without evidence.")
        else:
            report = _attempt(lambda: workspace.analyse(
                old, new, old_evidence=old_evidence if include_evidence else None,
                new_evidence=new_evidence if include_evidence else None,
            ))
            if report is not None:
                st.session_state["analysis"] = report
                st.session_state["analysis_ticker"] = (
                    old_evidence["ticker"] if old_evidence else old.ticker or new.ticker
                )
                st.session_state["analysis_synthetic"] = False
    report = st.session_state.get("analysis")
    if report is None:
        return
    st.divider()
    if st.session_state.get("analysis_synthetic"):
        st.warning("SYNTHETIC DEMO · Invented company and adjustment definitions; no real company analysis.")
    _summary(report)
    st.subheader("Visual Comparison")
    _counts(report, chart_key="drift_counts")
    st.subheader("Adjustment Comparison")
    rows = _comparison_rows(report)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Both confirmed adjustment sets are empty.")
    _analysis_evidence(report, key="drift_adjustment")
    _sources(report)
    st.subheader("Report Export")
    _download_analysis(report)
    st.caption("Methodology: this report compares user-confirmed adjustment sets. A set change "
               "does not independently establish a complete financial formula change. "
               "No fraud or investment risk judgment is provided.")


def _display_snapshot(snapshot: dict[str, Any]) -> None:
    st.success("Evidence Available")
    left, right = st.columns(2)
    left.caption("Company / Stock Ticker")
    left.write(f"{snapshot['company']} · {snapshot['ticker']}")
    right.caption("Fiscal Period / Metric")
    right.write(f"{snapshot['fiscal_period']} · {snapshot['metric_name']}")
    for label, value in (("Definition SHA256", snapshot["definition_sha256"]),
                         ("Report SHA256", snapshot["report_sha256"])):
        st.caption(label)
        st.code(value, language=None, wrap_lines=True)
    items = snapshot["items"]
    if not items:
        st.info("The confirmed definition contains no adjustments.")
        return
    name = st.selectbox("Adjustment", [item["canonical_name"] for item in items], key="evidence_adjustment")
    item = next(item for item in items if item["canonical_name"] == name)
    for entry in item["evidence"]:
        with st.container(border=True):
            st.markdown(f"**PDF Page {entry['page_number']}**")
            st.caption("Matched Text")
            st.code(entry["matched_text"], language=None, wrap_lines=True)
            st.caption("Original Evidence Excerpt")
            st.code(entry["evidence_excerpt"], language=None, wrap_lines=True)
    st.caption("SHA256 is a file identifier and consistency check, not a digital signature or "
               "third-party source certification. Viewing a historical snapshot does not "
               "reopen or verify the PDF's current contents.")


def _evidence_explorer(workspace: Workspace) -> None:
    st.subheader("Trace confirmed items to original text")
    st.caption("Validate a saved snapshot against its exact MetricDefinition bytes, or create "
               "one from a registered PDF. Snapshot JSON uploads: maximum 2 MiB.")
    definitions = _saved_definitions(workspace)
    if definitions is None:
        return
    if not definitions:
        st.info("No Evidence Attached")
        st.caption("Confirm candidates in Financial Metric Explorer, or import definitions in Drift Analysis.")
        return
    labels = {_definition_label(item): item for item in definitions}
    selection = st.selectbox("MetricDefinition", list(labels), key="evidence_definition")
    definition = labels[selection]
    widget_key = _upload_key("evidence_upload")
    st.file_uploader("Load Evidence Snapshot JSON", type=["json"], key=widget_key,
                     on_change=_remember_upload, args=(widget_key, "evidence_upload_data"),
                     max_upload_size=2)
    data = st.session_state.get("evidence_upload_data")
    upload_digest = None if data is None else hashlib.sha256(data).hexdigest()
    _sync_context("evidence_explorer_context", (_definition_identity(definition), upload_digest),
                  ("evidence_snapshot", "evidence_path", "evidence_error", "evidence_adjustment"))
    snapshots = st.session_state.setdefault("evidence_snapshots", {})
    if data is not None and st.session_state.get("evidence_snapshot") is None and not st.session_state.get("evidence_error"):
        validated = _attempt(lambda: workspace.import_evidence(data, definition),
                              failure_key="evidence_error")
        if validated is not None:
            snapshot, path = validated
            st.session_state["evidence_snapshot"] = snapshot
            st.session_state["evidence_path"] = path
            snapshots[definition.sha256] = (snapshot, path)
    elif data is None and definition.sha256 in snapshots:
        snapshot, path = snapshots[definition.sha256]
        st.session_state["evidence_snapshot"] = snapshot
        st.session_state["evidence_path"] = path
    if data is not None and st.button("Remove Uploaded Snapshot", key="clear_evidence_upload"):
        _reset_upload("evidence_upload", "evidence_upload_data")
        st.rerun()
    with st.expander("Create a fresh evidence snapshot from a registered PDF"):
        reports = _attempt(workspace.reports)
        if reports is not None:
            matching = [report for report in reports
                        if report.company.casefold() == definition.definition.company.casefold()
                        and report.fiscal_period == definition.definition.fiscal_period]
            report = _choose_report(matching, prefix="evidence_report")
            if st.button("Create Evidence Snapshot", key="create_evidence", disabled=report is None):
                result = _attempt(lambda: workspace.create_evidence(definition, report),  # type: ignore[arg-type]
                                  failure_key="evidence_error")
                if result is not None:
                    snapshot, path = result
                    st.session_state["evidence_snapshot"] = snapshot
                    st.session_state["evidence_path"] = path
                    st.session_state.pop("evidence_error", None)
                    snapshots[definition.sha256] = (snapshot, path)
    error = st.session_state.get("evidence_error")
    if error:
        st.error(f"Evidence Validation Failed: {error}")
        return
    snapshot = st.session_state.get("evidence_snapshot")
    if snapshot is None:
        st.info("No Evidence Attached")
        return
    _display_snapshot(snapshot)
    raw = _attempt(lambda: workspace.read_artifact(st.session_state["evidence_path"]))
    if raw is not None:
        st.download_button("Download Evidence Snapshot JSON", raw,
                           file_name=f"evidence_{snapshot['fiscal_period']}.json",
                           mime="application/json", key="download_evidence")


def main() -> None:
    """Render the five dashboard pages without importing Streamlit in core modules."""
    st.set_page_config(page_title="Metric Mirage", page_icon="◈", layout="wide",
                       initial_sidebar_state="expanded")
    # Interrupt widget cleanup for inputs on the other pages. Results live in
    # separate keys and are cleared only when their input fingerprint changes.
    for key in _PERSISTENT_INPUTS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.markdown(_CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("## ◈ Metric Mirage")
        st.caption("HONG KONG EQUITIES")
        page = st.radio("Research Workspace", PAGES, key="page", label_visibility="collapsed")
        st.divider()
        st.caption("LOCAL WORKSPACE")
        st.caption("Single-user research tool. Your uploaded files stay on this machine.")
        st.caption("Adjustment sets · Original text · Document provenance")
    st.title("Metric Mirage")
    st.caption("Financial Metric Forensics for Hong Kong Equities")
    st.divider()
    workspace = _attempt(Workspace)
    if workspace is None:
        return
    pages = dict(zip(PAGES, (_overview, _report_manager, _metric_explorer,
                            _drift_analysis, _evidence_explorer)))
    pages[page](workspace)
