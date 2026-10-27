# Xiaomi FY2023-FY2024: a reproducible real-report case

This case follows Xiaomi Corporation (Hong Kong code **01810**) through two official English annual reports, using the Group's explicitly named **Adjusted Net Profit**. Source annotation, candidate extraction, explicit selection, evidence snapshots, and cross-period comparison have been executed. Every selected item has an exact original-text excerpt and the correct one-based PDF page.

**Result:** the selected sets contain the same three direct adjustment categories in both periods: share-based payments, investment fair-value changes, and amortization of acquisition-related intangible assets. The existing comparison engine returns **0 added, 0 removed, 3 unchanged**, with `has_definition_drift=false`. This describes these selected sets; it does not establish that the complete financial formulas are identical.

**Review method:** AI-assisted source annotation followed by a separate AI verification pass. No independent human review or human timing study has been performed. The parser correction was motivated by these documents, so this is a development case, not a blind held-out benchmark or a general accuracy claim.

## Sources and locators

| Period | Official source | Total PDF pages | Definition | Reconciliation and notes |
| --- | --- | ---: | --- | --- |
| FY2023 | [2023 English annual report](https://www.hkexnews.hk/listedco/listconews/sehk/2024/0425/2024042500842.pdf) | 347 | PDF **26**, printed **24** | PDF **27**, printed **25** |
| FY2024 | [2024 English annual report](https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0424/2025042401119.pdf) | 379 | PDF **30**, printed **28** | Current-year table on PDF **30**; prior-year table and common notes on PDF **31**, printed **29** |

The source URLs, acquisition date, exact SHA256 values, and page counts are in [source_manifest.json](source_manifest.json). Issuer IR direct downloads returned HTTP 403 in the execution environment; the corresponding publicly accessible official HKEX disclosure documents were obtained instead. No access restriction was bypassed. Source PDFs are kept locally and are not redistributed in Git.

PDF page 1 is the cover. Printed page numbers are two lower for the reviewed passages. The FY2024 report's PDF page 31 contains the **FY2023 comparative table**, not the FY2024 table; this distinction is retained in the labels. Neither a different non-IFRS measure nor the separate Hardware Business metric was renamed as Adjusted Net Profit.

## Baseline annotation

[review_labels.csv](review_labels.csv) was frozen before the formal CLI run by a separate source-review pass that did not inspect generated candidates. Its SHA256 is `601a4c214ece164554b372c962876a436d16e6063dd357d707dc1476e15cc430`. The [labeling protocol](labeling_protocol.md) defines the review boundary, semantic mappings, sampled negatives, comparative-period handling, and signed reconciliation checks.

There are **36 evidence rows**: 14 supported, 8 outside the dictionary, and 14 negative. Supported rows comprise 12 direct occurrences and 2 nested investment-impairment occurrences. After report-concept deduplication, there are **8 supported concepts**: 6 direct and 2 nested across the two reports. The positive scope is the formal Group definition and reconciliation subsection; negatives are a purposeful sample, not every irrelevant passage in either report. Sixteen pages were visually reviewed, and both complete PDFs were read with strict `pypdf` extraction during verification.

The reports each explicitly name five top-level adjustments. Two are outside the six-class dictionary: changes in the value of liabilities payable to investors, and income-tax effects of adjustments. They remain visible in the labels and are not silently folded into investment fair-value changes. The investment-impairment provision is a nested component of the investment adjustment, not an independently quantified top-level adjustment; it is retained in the all-concepts recall denominator.

## Execution and the parser correction

The preserved [baseline commit](https://github.com/AndyLamyinwik/metric-mirage/commit/85e8429967d402b1572ca4ac5ed0cc9e34fa622a) produced **zero candidates** in each complete PDF. That is an extraction failure, not evidence of unchanged definitions. Baseline candidate JSON is retained in `results/`.

A narrow correction supports an affirmative definition of profit adjusted by adding back items, sequential `(i)`, `(ii)`, `(iii)` lists, and explicit full aliases for net investment fair-value changes and amortization resulting from acquisitions. The six canonical categories, comparison engine, confirmation API, and evidence schema are unchanged. Unsupported list entries still stop extraction, and the original negation, same-page, paragraph, sentence, and 600-character safeguards remain. Regression tests include negative and uncertain statements, ordinary expense text, unsupported qualifiers, and broken list structure.

The formal workflow used the actual CLI, in order:

1. Register both original PDFs in a new, private case registry.
2. Extract and save candidates from both the baseline and corrected source versions.
3. Explicitly select the three supported direct canonical items for each report.
4. Export comparison-compatible definitions and evidence snapshots.
5. Verify every stored excerpt against strict extraction of its recorded PDF page.
6. Compare the definitions with both snapshots attached, producing Markdown and JSON.
7. Score the raw candidates against the frozen labels, before any selection filtering.

Selections were made by the AI assistant under the project owner's delegation. They are not an independent verification of a complete formula. Exact runtime package versions, executed commands, per-step durations, source-module hashes, and result-file hashes are in [execution.json](results/execution.json). The source-module hash identifies the executed working-tree correction; `checkout_head_at_run` identifies its parent checkout, not an assertion that the correction existed in the original release.

## Candidate and evidence results

| Selected canonical item | FY2023 evidence | FY2024 evidence | Status |
| --- | --- | --- | --- |
| `amortisation_of_intangible_assets` | PDF 26 / printed 24 | PDF 30 / printed 28 | UNCHANGED |
| `fair_value_changes` | PDF 26 / printed 24 | PDF 30 / printed 28 | UNCHANGED |
| `share_based_payments` | PDF 26 / printed 24 | PDF 30 / printed 28 | UNCHANGED |

All **6 candidate entries and 6 snapshot entries** were checked against the original page text. Their `matched_text` and `evidence_excerpt` are exact substrings, including original line breaks. PDF hashes, definition-file hashes, and all 14 recorded result-file hashes matched. [traceability.json](results/traceability.json) records the programmatic checks; [review_record.json](review_record.json) records the separate AI verification and its boundaries.

Inspect the actual [FY2023 candidates](results/current.xiaomi-fy2023.candidates.json), [FY2024 candidates](results/current.xiaomi-fy2024.candidates.json), [FY2023 evidence](results/xiaomi-fy2023.evidence.json), and [FY2024 evidence](results/xiaomi-fy2024.evidence.json). The final comparison is available as [Markdown](results/drift.md) or [JSON](results/drift.json). The original wording differs slightly between years, and numerical adjustments change; neither establishes a category addition or removal.

## Evaluation

The primary page-occurrence key is `(report_id, pdf_page, canonical_item)`. Precision is `TP / (TP + FP)`; recall is `TP / (TP + FN)`. A zero denominator is **N/A**, not 100%. Emitted candidates outside the reviewed pages would remain unreviewed; this run emitted none. Every emitted candidate was separately checked for the correct relation and passage, rather than accepted merely because its concept appeared somewhere in the report.

| Version | TP | FP | FN | Precision | Page-occurrence recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0 | 0 | 14 | N/A | 0% |
| Corrected | 6 | 0 | 8 | 100% | **42.86%** |

Repeated table evidence makes page-occurrence recall different from report-level concept coverage. The separate deduplicated key is `(report_id, canonical_item, target_level)`:

| Supported concept scope | Recalled / expected | Recall |
| --- | ---: | ---: |
| All supported concepts, including nested impairment | 6 / 8 | **75%** |
| Direct concepts only | 6 / 6 | 100% |
| Nested impairment components | 0 / 2 | 0% |

The eight page-level misses are six reconciliation-table/continuation occurrences and two nested investment-impairment notes. The parser found each of the three direct concepts in the definition paragraphs, but did not parse all their table occurrences. Nested impairment wording and its footnote relationship remain unsupported by the current literal rules. Dictionary-external liabilities and tax effects are listed separately, not counted as false negatives for the six-class evaluation. The 14 sampled negative rows produced no candidates, but this does not establish a whole-document false-positive rate.

Download [evaluation_summary.csv](evaluation_summary.csv) for the metric table, [evaluation_items.csv](evaluation_items.csv) for each TP/FN and its reason, or [evaluation.json](results/evaluation.json) for the complete machine-readable results. These small, development-case scores do not estimate general financial-report extraction performance.

## Reproduce the case

Use a full Git checkout with the baseline commit available, **Python 3.11+**, and an existing environment. The recorded run used Python 3.13.15, pypdf 6.20.0, and fonttools 4.67.0. These PDFs require pypdf's optional CFF font support; missing font support is an extraction error, not a warning to suppress.

```sh
python -m pip install -e ".[research]" "pypdf[fonts]==6.20.0" "fonttools==4.67.0"
```

Download the two official reports linked above using a normal browser, retain them locally, and name them `FY2023.pdf` and `FY2024.pdf` in one directory. Verify that they match the recorded hashes; the script checks this before creating a run directory. Then, from the project root:

```sh
python scripts/reproduce_xiaomi_case.py --source-dir "<directory-containing-the-two-PDFs>" --output-dir .metric_mirage/case-runs/replay-001
```

The output directory must be new. The script does not download reports, alter the source PDFs, use your existing registry, or overwrite an earlier run. It archives the preserved baseline from Git, uses the same interpreter for both source versions, and executes every CLI step. Results are under the new directory's `results/`; the registry, archived baseline code, and raw logs stay in the private run directory. Raw logs can contain local absolute paths and should not be published.

Reproduced candidates, definitions, snapshots, and drift content should match the checked-in artifacts for these exact PDF bytes and dependency versions. Runtime timings and checkout identifiers can differ. `.gitattributes` preserves the exact bytes of the checked-in labels and hash-bound evidence; do not reformat definition JSON before validating its snapshot.

To inspect the saved comparison without downloading PDFs:

```sh
python -m metric_mirage drift docs/cases/xiaomi-2023-2024/results/xiaomi-fy2023.definition.json docs/cases/xiaomi-2023-2024/results/xiaomi-fy2024.definition.json --old-evidence docs/cases/xiaomi-2023-2024/results/xiaomi-fy2023.evidence.json --new-evidence docs/cases/xiaomi-2023-2024/results/xiaomi-fy2024.evidence.json
```

This last command validates captured snapshots; it does not reopen the PDFs. Reproduction with the original reports performs the separate source-text checks.

## Timing and limitations

The recorded CLI/subprocess steps total **21.55 seconds** on the local Windows environment, plus **0.26 seconds** for the additional source-substring checks. These figures exclude acquisition, source review, AI verification, tests, and packaging. They are not human review times or a measured speedup. `human_review_time_seconds` is explicitly `null`.

All **376 offline tests passed** in 68.322 seconds in the local validation run, including the original 351 tests, 13 add-back regressions, and 12 evaluation/traceability tests. Tests continue to use synthetic inputs; real source PDFs are excluded from Git and CI.

The case demonstrates a reproducible selected-set comparison with document-level and page-level evidence. It leaves table extraction, nested semantic mapping, dictionary-external categories, and human benchmark validation unresolved. SHA256 checks content identity, not issuer authenticity. The tool does not recalculate financial values, verify a complete formula, or assess fraud or investment risk. The signed reconciliation shown in the labeling protocol is a source-review check, not a new product calculation feature.

## 繁體中文摘要

這個案例使用小米 FY2023、FY2024 兩份官方英文年報，完整執行登記、候選提取、明確選取、證據快照及兩期比較。三個已選取的直接調整項目均能回到正確原文：FY2023 為 **PDF 第26頁／印刷第24頁**，FY2024 為 **PDF 第30頁／印刷第28頁**。結果為新增0項、移除0項、保留3項。

標註及另一輪來源核對均由 AI 協助完成。評估保留了漏抓：頁面層級召回率42.86%；按每份報告的概念去重、包含嵌套減值項後，召回率為75%。上述分數只適用於這個開發案例，不能作為一般準確率。真人覆核時間未量測，也沒有宣稱省時比例。依上方重現步驟下載原始PDF、安裝字型依賴並執行腳本，即可重跑；原始PDF與私人registry均不在Git倉庫中。
