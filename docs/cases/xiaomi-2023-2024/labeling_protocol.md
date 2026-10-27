# Xiaomi FY2023-FY2024: labeling protocol

This is a bounded, AI-assisted source review for the Group's **Adjusted Net Profit**. Every label is **AI-assisted; pending independent human review**. It is not a human gold standard. Independent human review has not been completed, and human review time is **not measured**. No time-saving percentage can be inferred from this case.

## Sources and page numbering

| Report ID | Official public annual report | SHA-256 | PDF pages |
| --- | --- | --- | ---: |
| `xiaomi-fy2023` | [Xiaomi 2023 annual report, HKEX](https://www.hkexnews.hk/listedco/listconews/sehk/2024/0425/2024042500842.pdf) | `76e579dbc5c01372bef6efb8e4ae0e96ada17336a2b32b5714bc07506534aec3` | 347 |
| `xiaomi-fy2024` | [Xiaomi 2024 annual report, HKEX](https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0424/2025042401119.pdf) | `9fbb8a9baa8bb9b559ae3789080e56cd498ab1f0db6d8853ff2a63c598ccad11` | 379 |

`pdf_page` is one-based: the first PDF page is 1, including the cover. `printed_page` is the page number visibly printed in the report. For all labeled pages, the PDF page is two greater than the printed page. The FY2023 definition is **PDF 26 / printed 24**; its reconciliation is **PDF 27 / printed 25**. The FY2024 definition and current-year reconciliation are **PDF 30 / printed 28**, followed by the prior-year table and common notes on **PDF 31 / printed 29**. These locators were checked against rendered pages, not inferred from search snippets.

## Review boundary and independence

The reviewer read the existing six canonical concept names and their alias dictionary, then independently inspected the source PDFs before the formal candidate run. No generated candidate output was used to select, add, remove, or change labels. No extraction rule was modified by this review. The baseline intentionally includes semantic wording that an existing literal alias may miss.

Discovery used `pypdf` to extract every page and search for the metric and concept terms: share-based compensation/payments, fair-value changes, impairment, listing expenses/costs, acquisition-related expenses/costs, and amortization/amortisation of intangible assets. The full definition and reconciliation subsections, their continuation pages, and their footnotes were read together. Source review also examined the chairman's references, nearby Hardware Business Net Margin formulae, and sampled accounting-policy and expense notes. The FY2023 pages selected for visual checks were 10, 17, 26-28, 215, and 267-268; the FY2024 pages were 10, 19, 30-32, 244, and 299-300. Poppler page renders were inspected to resolve table headers, footnote scope, signs, amounts, and printed page numbers. Extraction produced a missing-fontTools warning; the relevant evidence was checked visually, including the broken extraction of the word `impairment` across a line in the investment footnote.

The positive scope is the Group's formal Adjusted Net Profit definition and reconciliation subsection. The negative examples are a purposeful sample of realistic boundary mistakes, not all irrelevant passages in the reports. The chairman's EV footnotes describe the segment expense or segment adjusted loss included in Group adjusted net profit; they are not treated as additional Group adjustment categories. The Hardware Business metric is a separate measure. This corpus is not an exhaustive annotation of every truth, every metric, every duplicate occurrence, or every negative passage in either annual report. Counts from it must be described as **bounded AI-assisted review results**.

## Row meaning and decisions

One CSV row records a specific evidence occurrence. `fiscal_period` identifies the period to which that occurrence applies; consequently, the FY2023 comparative table in the FY2024 report has `fiscal_period=FY2023`. Footnotes shared by current and prior-year reconciliations are recorded once under the report's current fiscal period, with their joint scope stated in `notes`. The repeated definition and reconciliation evidence is retained for page-level auditing and must not be counted as additional distinct financial concepts.

`metric_text` names the metric to which local evidence is connected. It is empty for ordinary accounting-policy or expense-note occurrences that do not name a metric. The continuation-table rows inherit the Group Adjusted Net Profit heading from the preceding definition page. `adjustment_text` is a short source phrase with line breaks and spacing normalized; the EV phrase also normalizes the parenthetical acronym punctuation. `notes` contains review interpretation and factual amount checks. Amounts in the reconciliation are RMB thousand unless stated otherwise.

| Field or label | Meaning |
| --- | --- |
| `supported` | Explicit Group definition, reconciliation, or attached explanatory note supports the adjustment and its semantic mapping to one of the six canonical concepts. |
| `unsupported` | A real, explicitly named Group adjustment is outside the six canonical concepts. This is an ontology boundary, not an extraction false negative for those six classes. `canonical_item` is empty. |
| `negative` | The occurrence does not establish the target adjustment relation: ordinary expense recognition, a different metric, a broader amount, or an amount explicitly included rather than excluded. It does not deny that a concept may be supported elsewhere. |
| `target_level=direct` | A top-level named adjustment in the Group definition or reconciliation. This also applies to outside-ontology `unsupported` rows. |
| `target_level=nested` | A disclosed component of a named top-level adjustment. It must not be reported as a separately quantified top-level adjustment. |
| `target_level=negative` | A sampled negative evidence occurrence. |
| `label_origin` | Always `AI-assisted; pending independent human review`. |

The semantic mapping is independent of parser coverage. In particular, **the impairment provision for investments** maps to `impairment_losses`, because the reconciliation footnote explicitly includes that impairment provision within the investment fair-value adjustment. The phrase is not a separate top-level adjustment, and no independent amount for the component is stated there. Amortization resulting from acquisitions maps to `amortisation_of_intangible_assets`; it must not also be relabeled as `acquisition_related_expenses`.

Changes in the value of financial liabilities payable to investors remain `unsupported` here. The report names this separately from net fair-value changes **on investments**; the six-concept dictionary does not establish that all liability remeasurement should be merged into the investment fair-value class. Income tax effects also remain outside the six classes.

## Six-class coverage

| Canonical concept | FY2023 | FY2024 | Interpretation |
| --- | --- | --- | --- |
| `share_based_payments` | Supported direct | Supported direct | Share-based compensation. Ordinary employee-benefit totals and Hardware Business exclusions are separate negative occurrences. |
| `fair_value_changes` | Supported direct | Supported direct | Net fair-value changes on investments. The IFRS income-statement line alone is insufficient and has a different amount. |
| `impairment_losses` | Supported nested | Supported nested | Investment impairment provision in the explanatory note; not all inventory or financial-asset impairment expenses. |
| `listing_expenses` | No positive observed | No positive observed | No matching listing expense/cost disclosure was found in the reviewed metric material or the concept-term discovery scan. No artificial source row is added. |
| `acquisition_related_expenses` | No positive observed | No positive observed | The accounting policy says acquisition-related costs are expensed as incurred; this does not establish a non-IFRS adjustment. |
| `amortisation_of_intangible_assets` | Supported direct | Supported direct | Only the acquisition-related subset is named as an adjustment, not the entire ordinary amortization expense. |

The sources therefore support **four of the six canonical concepts per report**, comprising three direct concepts and one nested component. They do not provide six genuine positive classes. No positive was fabricated to fill an absent class.

## Period comparison from the sources

Both reports have the same five top-level adjustment descriptions. The FY2024 wording changes the base from profit for the period to profit for the year and shortens fund investors to investors. The FY2024 notes additionally explain the employee share-based payments. These wording differences do not show an addition or removal of any of the six canonical concepts. The FY2023 values reproduced in the FY2024 comparison table agree with the FY2023 report's non-IFRS reconciliation.

| Reconciliation, RMB thousand | FY2022 comparison in 2023 report | FY2023, reproduced in both reports | FY2024 current period |
| --- | ---: | ---: | ---: |
| Profit for the year | 2,502,568 | 17,474,197 | 23,578,449 |
| Share-based compensation | 2,467,224 | 3,344,357 | 3,719,482 |
| Net fair-value changes on investments | 4,405,700 | (2,746,397) | 1,184,519 |
| Acquisition-related intangible amortization | 144,271 | 144,008 | 144,008 |
| Value changes of liabilities to investors | (583,862) | 410,946 | (1,243,607) |
| Income tax effects | (417,894) | 645,643 | (148,320) |
| Adjusted Net Profit | 8,518,007 | 19,272,754 | 27,234,531 |

The signed arithmetic reconciles in each column. The group profit base in this non-IFRS table is the stated reconciliation value; the report's consolidated statements can differ slightly in last-digit presentation. Parentheses mean negative adjustments. A category being described as added back does not imply a positive signed adjustment every year. A change of sign or amount is not a change in the definition.

## Evaluation and freeze

The CSV contains 36 evidence rows: 14 supported (12 direct and 2 nested), 8 unsupported, and 14 negative. At the distinct concept level, there are 8 supported report-concept pairs: 6 direct and 2 nested. The unsupported rows represent 4 distinct report-adjustment pairs, repeated across definition and reconciliation evidence. Negative sampling is not a representative estimate of the reports' total false-positive opportunities.

For **concept recall**, deduplicate supported rows by `(report_id, canonical_item, target_level)`. Report all supported concepts together (denominator 8), direct concepts separately (denominator 6), and nested concepts separately (denominator 2). A failure to identify the investment impairment component is a false negative in the all-concepts and nested measures, even if all three direct classes are identified. Do not shrink the main denominator to literal aliases that the parser happens to support. These are descriptive case results, not a population estimate or a human-verified accuracy claim.

For **page-level evidence auditing**, keep the page and fiscal period and distinguish actual target passages from ordinary expense occurrences on the same or another page. A page-level hit is not proof that every amount or every occurrence of that concept is correct. Candidate passages outside the bounded labeled scope require additional source review; they are not automatically true or false. Precision requires adjudicating each emitted candidate's actual relation and passage, not simply seeing that its canonical concept is supported somewhere in the report. Do not count ordinary expense notes as evidence for an adjustment, and do not count an unimplemented outside-ontology adjustment as a six-class extraction failure.

This baseline was frozen before the formal candidate run on 2026-10-27. The frozen `review_labels.csv` SHA-256 is **`601a4c214ece164554b372c962876a436d16e6063dd357d707dc1476e15cc430`**. Any subsequent source-based correction must be recorded as a new baseline version with a reason and a new hash; it must not be silently edited after seeing results.
