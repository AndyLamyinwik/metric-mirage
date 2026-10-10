# Contributing to Metric Mirage

Contributions should improve reliable, source-grounded examination of financial metric definitions while preserving the distinction between rule-extracted candidates, user-confirmed adjustment sets, and original evidence.

## Development Setup

Use Python 3.11 or later. From a source checkout:

```sh
python -m venv .venv
```

Activate `.venv` with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell or `source .venv/bin/activate` on macOS/Linux, then install development dependencies:

```sh
python -m pip install -e ".[dev]"
```

## Checks

```sh
python -m unittest discover -s tests -v
python -m metric_mirage demo
python -m build
python -m streamlit run app.py
```

For dashboard changes, inspect all affected pages, empty states, error messages, narrow-screen layout, and downloaded files. Add meaningful offline tests for changed behavior, including `AppTest` when needed. The CI matrix covers Python 3.11, 3.12, and 3.13 on Ubuntu.

## Submitting Changes

- Keep changes focused and preserve existing public APIs unless a compatibility change is explicitly agreed.
- Explain the problem, resulting behavior, and checks actually performed in the pull request. Record any environment or behavior you did not verify.
- Update documentation when commands or user-facing behavior change.
- Use synthetic inputs and temporary directories for tests. Do not commit real company PDFs, local registries, evidence snapshots, private definitions, credentials, or developer-specific paths.
- Preserve raw evidence and reproducible sorting. Do not infer unsupported adjustments, complete formulas, fraud, or investment risk from keyword presence or set differences.
- Check the diff and Git status before submitting. Keep the existing MIT license.

For suspected vulnerabilities, follow [SECURITY.md](SECURITY.md) rather than posting sensitive details in a public issue.
