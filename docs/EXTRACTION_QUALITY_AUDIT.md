# Offline extraction quality audit

This utility uses only local JSON, SHA-256, CSV and PyMuPDF rendering. It imports
no OpenAI client or ingestion modules and has no network or publishing path. No
new dependencies. All artifacts and human corrections remain experimental with
`needs_review` and `ready_to_publish=false`.

From the Desktop OrderAI project:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.extraction_quality_audit generate "tests\fixtures\burger_and_sauce_menu.pdf"
```

The utility selects the most recently modified complete multipage draft from
`tests/output/multipage_pdf`. It checks the saved PDF SHA-256, page provenance,
unique product IDs and unchanged Stage 1 source fields/groups. If the newest
complete draft has a mismatching PDF hash it rejects the audit, rather than
silently falling back to an older draft. Use `--draft "path\draft.json"` to pin a
specific complete draft, or `--draft-dir "path\directory"` to select elsewhere.

Each run creates a unique `tests/output/quality_audit/audit_<timestamp>_<id>/`:

- `page_01.png`, etc.: original pages rendered locally at 160 DPI.
- `comparison.html`: static, local side-by-side page images and products, with
  expandable original Stage 1 groups and Stage 2 evidence/proposals. No server,
  dashboard or external resources. Open the file in a browser.
- `inventory.csv` / `inventory.json`: page/category-grouped names, descriptions,
  prices/currencies, source text, IDs and Stage 2 statuses. JSON also contains
  complete groups, components, ambiguity explanations and original category labels.
- `human_review.csv`: editable checklist, initially one pending confirmation per
  product. Nothing is pre-confirmed; evidence/reviewer fields start blank.
- `findings.json`: original review issues grouped into pricing/currency,
  names/categories/descriptions, choices/references, pipeline/publication and other
  owner-facing categories, plus new portion/component and relationship comparisons.
- `original_audit.json`: byte-identical complete draft copy retaining the full
  original audit trail, including per-page original findings and recovery details.
- `manifest.json`: input paths/hashes, image list, summary and publication block.

Relationship comparisons identify unmatched Stage 1 groups, changes to group
structure, named choices absent from candidate Stage 2 groups and added proposals.
Group correspondence uses conservative lexical candidates, not semantic truth.
Stage 1 groups can be wrong: every flag requires PDF comparison, and no group is
automatically restored. Portion heuristics compare explicit numeric wording
matching a product's final word with component representation. They can miss
portions, especially sizes/volumes, and can flag legitimate deal elements. Inclusion
or selection wording with no corresponding Stage 2 fields is also flagged without
declaring a proven omission. These are observations, not measured accuracy.

## Record manual findings

Edit `human_review.csv` in a text editor or spreadsheet, preserving column headers
and UTF-8 CSV format. Original IDs, pages and extracted values provide context.
Source-derived formula-like CSV text is prefixed with an apostrophe for spreadsheet
safety; unmodified raw values remain in inventory JSON and the original audit.

Decisions: `pending` (default), `uncertain` (not ground truth), `confirmed`,
`false_positive` (only for product confirmations). Every confirmed finding needs a
valid page, reviewer name and manually entered evidence, e.g. a printed label and
its page position. Mark uncertainty rather than inventing an unreadable price.

Record types:

| record_type | How to use |
| --- | --- |
| product_confirmation | Confirm the extracted product exists, or mark false_positive. Keep its ID/page. Optional verified_name/category/price fields record corrections. |
| omission | Append a confirmed row with blank item_id, actual page, verified_name, manual evidence and reviewer. Distinguish separate products in the name/evidence. |
| category_correction | Append a confirmed row with existing item_id/page and verified_category. |
| verified_price | Append a confirmed row with existing item_id/page, nonnegative verified_price and explicit verified_currency. |
| page_complete | After comparing every visible product and recording omissions, append a confirmed row with blank item_id, page, evidence describing the complete census and reviewer. |

Process edited CSV without modifying the draft:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.extraction_quality_audit review "tests\output\quality_audit\audit_<timestamp>_<id>"
```

An alternative edited file can be supplied with `--csv "path\review.csv"`. Each
review creates a separate `review_results_<id>.json` with manual records, original
input hashes, the reviewed CSV snapshot hash and conditional metrics. It never
modifies the original draft, inventory, page images or CSV. Changed source inputs,
invalid pages/IDs, missing manual evidence/reviewer, duplicate confirmations,
duplicate omission rows or invalid prices are rejected.

## Metrics and limits

Before manual verification: `accuracy_metrics=null`. Extracted counts, passed
validation and comparison flags are not recall or semantic accuracy.

Manual product confirmations/false positives permit **precision on the reviewed
subset only**, with its denominator reported. Recall is reported only when every
extracted product is manually classified and all pages have explicit `page_complete`
records. It is labelled **recall on a reviewer-asserted complete census**, using
confirmed products and confirmed omissions. Neither metric measures relationships,
prices, descriptions or overall semantic correctness. Reviewer assertions remain
the responsibility of the human; the utility cannot prove that the census is complete.

The static report and owner grouping preserve evidence rather than making decisions.
No automatic product matching, category approval, price inference, modifier repair
or production integration is provided. Large per-page images and duplicated audit
evidence can consume disk space. Inputs are verified by hashes, not authenticated
against deliberate local tampering. An interrupted generation may leave a partial
folder without a completed manifest; do not treat it as a completed audit.

Full relevant offline tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_visual_pdf_import tests.test_relationship_interpreter tests.test_multipage_pdf_import tests.test_relationship_recovery tests.test_extraction_quality_audit -v
```
