# Metric Mirage

**Financial Metric Forensics for Hong Kong Equities**

Track how financial metric definitions evolve across reporting periods, with source-grounded evidence from corporate disclosures.

![Overview showing the SYNTHETIC FY2024 to FY2025 comparison](docs/images/overview.jpg)

*Actual dashboard: the SYNTHETIC demo produces 1 added, 0 removed, and 2 unchanged adjustments.*

## Project Introduction

Metric Mirage is a local research tool for examining the adjustment items users confirm from Hong Kong listed-company disclosures. Register a PDF, inspect its extracted text, review conservative Adjusted Net Profit candidates, explicitly select adjustments, and compare the resulting definitions across periods. Optional evidence snapshots retain original excerpts, PDF page positions, and file hashes.

The comparison describes changes in user-confirmed adjustment sets. It does not independently establish that a company's complete financial calculation formula changed.

## Key Features

- Five-page Streamlit dashboard with Plotly charts and a ready-to-use **SYNTHETIC** demo.
- Hong Kong stock code normalization and a local PDF registry with SHA256 identifiers.
- Literal English, Traditional Chinese, and Simplified Chinese search in extractable PDF text.
- Rule-based Adjusted Net Profit candidates, explicit confirmation, and compatible definition JSON exports.
- Deterministic adjustment-set comparisons with Markdown and JSON reports.
- Optional validated evidence snapshots with original text and PDF page positions.
- Python APIs and a command-line interface using the same analysis functions as the dashboard.

## How It Works

1. **Register** a local PDF you have the right to process, with its stock code, company, period, report type, and source description.
2. **Inspect** keyword matches and adjustment candidates with their original evidence.
3. **Confirm** the specific candidates you intend to retain; selection starts empty.
4. **Compare** two saved definitions to find added, removed, and unchanged adjustments.
5. **Trace** the comparison to captured evidence snapshots when both periods' snapshots are available.

Registration supports `annual`, `interim`, `annual_results`, and `interim_results`. Codes such as `700`, `0700.HK`, and `00700` normalize to `00700`; formatting validation does not verify a listing.

## Quick Start

Requires **Python 3.11+**. From a source checkout, with your Python environment activated:

```sh
python -m pip install -e .
python -m streamlit run app.py
```

Open the local URL printed by Streamlit, normally `http://localhost:8501`. On **Overview**, select **Explore Demo**. No financial report, API key, or external service configuration is needed to run the bundled demo after installation.

Prefer the terminal? Run the separate synthetic Adjusted EBITDA example:

```sh
python -m metric_mirage demo
```

## Dashboard

| Page | Purpose |
| --- | --- |
| Overview | Current analysis, adjustment counts, a comparison chart, and Explore Demo. |
| Report Manager | Upload and register a PDF; inspect its metadata and SHA256. |
| Financial Metric Explorer | Search PDF text, inspect candidates, select adjustments, and download a confirmed definition. |
| Drift Analysis | Choose saved definitions or upload JSON; compare periods and download Markdown or JSON, with optional paired evidence. |
| Evidence Explorer | Create or load a definition's snapshot and inspect original excerpts, PDF pages, and hashes. |

Results remain available across page navigation in the current session. Changing the relevant report or input clears results that no longer apply. The interface distinguishes **Evidence Available**, **No Evidence Attached**, and **Evidence Validation Failed**.

Uploads and exports are stored under the Git-ignored `.metric_mirage/` directory: `uploads/`, `definitions/`, `evidence/`, and the `reports.json` registry. PDF uploads are limited to **25 MiB**; JSON uploads to **2 MiB**. Generated storage names prevent uploaded filenames from becoming filesystem paths. Existing output files are not overwritten.

The dashboard is intended for **local, single-user use**. It has no authentication or tenant isolation. The supplied Streamlit configuration binds the server to `127.0.0.1`; keep it on a trusted local interface. It is not configured as a public multi-user service. A trusted operator can set `METRIC_MIRAGE_DATA_DIR` before startup to change the storage directory. Supply that directory's registry path explicitly when using the CLI.

## Demo Walkthrough

1. Select **Explore Demo** on Overview.
2. Inspect the **SYNTHETIC** Adjusted Net Profit definitions for FY2024 and FY2025.
3. FY2024 includes `share_based_payments` and `amortisation_of_intangible_assets`. FY2025 also includes `listing_expenses`.
4. The existing comparison engine returns **1 added, 0 removed, and 2 unchanged** adjustments.
5. Open **Drift Analysis** to inspect the table and download Markdown or JSON.

All demo company details and definitions are invented. The demo shows **No Evidence Attached** and supplies no invented quotations, PDF pages, or real-company results. The CLI demo uses the separately bundled synthetic Adjusted EBITDA definitions.

## Financial Definition Drift

A definition JSON uses the existing five fields:

```json
{
  "company": "Synthetic HK Company",
  "metric_name": "Adjusted Net Profit",
  "fiscal_period": "FY2024",
  "adjustments": ["share_based_payments", "amortisation_of_intangible_assets"],
  "source": "SYNTHETIC: invented definition; no report evidence attached."
}
```

In Drift Analysis, choose the older definition first. Drift reports require exact periods such as `FY2024` and `FY2025`, with the new year later than the old year, and compatible company and metric names. The general `compare` command also accepts other period labels.

This **SYNTHETIC** table illustrates the dashboard demo:

| Adjustment | Old Period | New Period | Status |
| --- | --- | --- | --- |
| amortisation_of_intangible_assets | Yes | Yes | UNCHANGED |
| listing_expenses | No | Yes | ADDED |
| share_based_payments | Yes | Yes | UNCHANGED |

`has_definition_drift` is `true` when the normalized added or removed set is nonempty. Names receive basic whitespace normalization and Unicode case folding; synonyms are not inferred. Lists and tables use stable sorting. Reports preserve both full `source` strings and remain usable without attached evidence.

![Drift Analysis showing the SYNTHETIC comparison table and report downloads](docs/images/drift-analysis.jpg)

*Actual dashboard: the demo comparison has No Evidence Attached and remains downloadable as Markdown or JSON.*

## Evidence Traceability

Confirmation includes the registered source description and PDF SHA256 in the definition's `source`. Evidence export rechecks the current PDF hash, extracts original candidate evidence for every selected item, and binds the snapshot to the **exact definition-file bytes** through a separate SHA256. A formatting-only edit to definition JSON therefore invalidates its snapshot association.

Create a snapshot in Evidence Explorer, or upload one alongside its associated definition. Validation checks the schema, hashes, company, period, metric, source, and confirmed adjustment set. Evidence-backed drift requires valid snapshots for both periods. The viewer preserves matched text and original excerpts; page numbers start at **1** and refer to the PDF's page positions, which may differ from printed page labels.

SHA256 provides content identity and consistency checks, **not a digital signature or proof of publisher authenticity**. Drift reads captured snapshots rather than reopening the PDFs. An item absent from a user's confirmed set does not establish that it was absent from the original report.

![Evidence Explorer showing No Evidence Attached in an empty local workspace](docs/images/evidence-explorer.jpg)

*Actual dashboard: an empty workspace shows No Evidence Attached; no quotations or PDF pages are invented.*

## CLI Usage

The bundled comparison can be run immediately from the checkout:

```sh
python -m metric_mirage demo
python -m metric_mirage compare examples/old.json examples/new.json
python -m metric_mirage compare examples/old.json examples/new.json --format json
```

The following workflow requires your own local PDF and metadata. Replace `report_2024.pdf`, the company, code, and source description with values for a report you may process:

```sh
python -m metric_mirage reports add report_2024.pdf --ticker 0700 --company "Your company" --type annual --period FY2024 --source "Issuer IR"
python -m metric_mirage reports list 00700 --format json
python -m metric_mirage reports search 00700 --period FY2024 --keyword "經調整淨利潤" --max-results 10
python -m metric_mirage reports candidates 00700 --period FY2024 --format json
```

After reviewing the candidates, explicitly select names that actually appear in that report:

```sh
python -m metric_mirage reports confirm 00700 --period FY2024 --select share_based_payments --select amortisation_of_intangible_assets --output definition_2024.json
python -m metric_mirage reports evidence 00700 --period FY2024 --definition definition_2024.json --output evidence_2024.json
```

Repeat registration, review, confirmation, and optional evidence export for FY2025, then compare:

```sh
python -m metric_mirage compare definition_2024.json definition_2025.json
python -m metric_mirage drift definition_2024.json definition_2025.json
python -m metric_mirage drift definition_2024.json definition_2025.json --format json
python -m metric_mirage drift definition_2024.json definition_2025.json --old-evidence evidence_2024.json --new-evidence evidence_2025.json
```

Report commands accept `--registry PATH`; search, candidates, confirmation, and evidence also accept `--type` to narrow an ambiguous selection. Multiple matching reports produce an explicit error. Exports require an existing parent directory, a new output filename, and a filesystem supporting hard links; unsupported output volumes fail without leaving a partial final file. CLI registration retains the original PDF path and does not copy or modify the report.

JSON reports contain no extra stdout messages. Expected input errors produce helpful stderr messages and nonzero exit codes. See `python -m metric_mirage --help` and each subcommand's `--help` for options.

The public comparison API remains available independently of the dashboard:

```python
from metric_mirage import MetricDefinition, compare_metrics, normalize_hk_ticker

old = MetricDefinition("Synthetic company", "Adjusted Net Profit", "FY2024",
                       ["share_based_payments"], "SYNTHETIC: old definition")
new = MetricDefinition("Synthetic company", "Adjusted Net Profit", "FY2025",
                       ["share_based_payments", "listing_expenses"],
                       "SYNTHETIC: new definition")
diff = compare_metrics(old, new)
assert diff.added_adjustments == ("listing_expenses",)
assert normalize_hk_ticker("0700.HK") == "00700"
```

## Installation

Create an isolated environment before the Quick Start commands:

```sh
python -m venv .venv
```

Activate it in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
```

Then run `python -m pip install -e .` from the project root. Declared runtime dependencies are `pypdf`, Streamlit, and Plotly. No API key is needed. The root `app.py` is the source-checkout dashboard launcher; installed package APIs and the CLI are also usable outside the checkout.

For development and local distribution builds:

```sh
python -m pip install -e ".[dev]"
python -m build
```

## Testing

```sh
python -m unittest discover -s tests -v
```

Tests use temporary directories, synthetic JSON/PDF inputs, and mocked page text where appropriate. They cover the core APIs, CLI, report registration, search, candidates, explicit confirmation, file safety, evidence association, Markdown/JSON exports, and Streamlit integration with `AppTest`. Financial APIs, real reports, API keys, and paid services are not required.

The [GitHub Actions workflow](.github/workflows/ci.yml) is configured for Ubuntu with Python **3.11, 3.12, and 3.13**, including tests, package builds, CLI Demo, and Streamlit imports. Hosted results must be checked in GitHub Actions after publication; local checks do not establish a successful cloud run.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance and [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment boundaries.

## Methodology

Candidate extraction currently supports **Adjusted Net Profit**, including its explicit English, Traditional Chinese, and Simplified Chinese metric aliases. It uses a small dictionary and conservative, limited same-page context to identify adjustment relationships. Supported canonical items are:

- `share_based_payments`
- `fair_value_changes`
- `impairment_losses`
- `listing_expenses`
- `acquisition_related_expenses`
- `amortisation_of_intangible_assets`

Candidates remain separate from definitions until a user explicitly selects them. Comparison applies set differences to normalized adjustment names and does not recalculate financial values. Evidence preserves extracted text rather than paraphrasing it. A reported set change is neither a complete formula audit nor a financial-fraud or investment-risk assessment.

## Limitations

- PDF text extraction depends on text layers and font encoding. Scanned documents and some Chinese fonts may not produce usable text; there is no OCR.
- Conservative candidate rules can miss valid wording, tables, and relationships spanning pages. An empty candidate list means no supported relationship was established, not that the metric has no adjustments.
- User selections may be incomplete. Missing selected items do not prove missing disclosure, and set changes do not independently prove changes in a complete financial formula.
- Snapshots are local captured evidence, not signed authenticity proofs or live-PDF verification.
- The dashboard is a local single-user tool with no authentication or tenant isolation. Do not treat it as a public multi-user deployment.
- Only local reports supplied under the user's processing rights are supported; reports are not automatically downloaded.
- **Real-world PDF validation not performed**: the included fixtures and demo are synthetic, not evidence of performance on a particular issuer's disclosures.

## License

Metric Mirage is available under the [MIT License](LICENSE).
