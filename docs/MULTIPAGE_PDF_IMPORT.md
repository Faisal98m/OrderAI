# Visual PDF ingestion v2: sequential multi-page experiment

From the OrderAI project in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.multipage_pdf_import "tests\fixtures\burger_and_sauce_menu.pdf" --max-pages 10
```

This is a paid API command when checkpoints are unavailable. Development/testing
did not run it against the live API. The supplied fixture has four pages.

Flags: `--max-pages` defaults to 20 and rejects the entire PDF before any API
request when exceeded. It does not truncate the menu. `--vision-model` and
`--relationship-model` default to `gpt-4o`. `--no-cache` bypasses checkpoint reads
and writes. The existing `.env`/`OPENAI_API_KEY` configuration is reused. V2
disables SDK retries via the existing Stage 2 client factory. No new packages.

Outputs are unique files beneath `tests/output/multipage_pdf/`:

- `complete_draft_<timestamp>_<uuid>.json` when every page was processed.
- `incomplete_draft_<timestamp>_<uuid>.json` after a processing failure.
- `checkpoints/vision_<fingerprint>.json` and
  `checkpoints/relationships_<fingerprint>.json` for reusable intermediates.

Complete means the processing steps finished, not that extraction is factually
complete or approved. All outputs have `needs_review` and `ready_to_publish=false`.
The CLI exits 0 for processed drafts and 1 for incomplete/rejected runs. It never
overwrites a production `menu.json` or older draft. A page failure stops subsequent
API calls; failed and unattempted pages are explicitly recorded. Any extracted
Stage 1 products from a failed Stage 2 page are retained as `not_completed` for
review. ID collisions abort consolidation instead of silently replacing items.

## JSON structure

- `menu`: consolidated category containers and unchanged product IDs/fields,
  Stage 2 relationship/semantic sidecars, unassociated modifier groups and source
  PDF path/hash/page count. Category sources preserve each page's label,
  description and ambiguities. Case/whitespace-equivalent category labels share
  a container; this implies no modifier or product relationship. Conflicting
  descriptions stay in the sources and generate a review finding.
- `pages`: ordered one-based page records with status, original structured vision
  `extraction`, `stage1_draft`, and `enriched_draft` when Stage 2 completed. Page
  drafts retain the existing audit findings and deduplicated review queues.
- `completion_status`: complete/incomplete, independently of publication status.
- `possible_duplicates`: products with case/whitespace-equivalent names, including
  IDs, categories, pages and source text. Products are never merged automatically.
- `reference_analysis`: unapproved candidates for Stage 2 unresolved range references.
- `validation`: existing menu validator results, unconditional publication blocker,
  consolidated page review queue and duplicate/metadata/reference findings.
- `review_summary`, `request_counts`, `checkpoint_hits`: processing/review totals
  and actual v2 request/cache counts.

IDs retain Stage 1's page/category/item index naming. They are stable for the same
validated extraction/checkpoints; a fresh model extraction can reorder categories
or products and change IDs. Consolidation checks uniqueness across every page.

## Reference candidates

`menu_reference_resolver.reference_candidates(menu)` is deterministic and makes
no API calls. Each reference preserves its original text, source evidence, item ID,
page and indexed Stage 2 group path. It remains `unresolved_reference` and
`requires_human_approval=true`, even when there is only one exact candidate.

Candidates contain target type (category/product), category index/name, product
ID/name when applicable, source page, supporting source fields, matched label,
shared tokens, match type and `approved=false`. `exact_label` means only that the
reference and extracted label match after case/whitespace normalization;
`approximate_lexical` uses shared words with simple plural handling. Neither proves
membership in a selectable range. The resolver never changes original proposals
or fills any choices. Ambiguous/no-match references remain in the review queue.

## Reuse and request counts

Run the same command again to resume; valid checkpoints avoid repeated requests.
Fingerprints bind vision results to PDF bytes, page, DPI, model, prompt and schema.
Relationship fingerprints additionally bind the exact input text, prompt, schema
and model to the page's extraction fingerprint. Cached payload checksums and schema
validation must pass. Relationship grounding is checked again; Stage 2 semantic
validation and review reporting are rebuilt on every run. PDF mutation during a
run fails safely. Invalid/corrupt checkpoints are not reused.

With no cache: **one vision request per page, plus one text request per extracted
product**. A page with zero products makes no Stage 2 requests but retains its
`no_items_found` review finding. All validated cache hits make zero model calls.
Existing v1 standalone outputs are preserved but not automatically imported as
checkpoints because they lack the v2 PDF/prompt/input fingerprints. A model alias
may change remotely; use `--no-cache` when a fresh extraction is needed.

## Offline tests and limits

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_visual_pdf_import tests.test_relationship_interpreter tests.test_multipage_pdf_import -v
```

Tests generate local PDFs and mock model results. They cover sequence/provenance,
consolidation, duplicates/collisions, unknown data, references, partial failures,
checkpoint reuse/invalidation/grounding, PDF limits/integrity/mutation, production
protection and existing single-page behaviour. No paid extraction is run.

Limits: no dashboard, database, approvals or publishing; no automatic product
merging, cross-page range expansion or inferred prices. Lexical candidate search
can miss synonyms and produce false positives. PDF text/vision can omit facts.
Large pages may consume substantial memory/tokens. Evidence comparisons and
selection-limit grammar retain their existing conservative restrictions. JSON
includes full per-page drafts as well as the merged view, so files can be large.

## Product recovery update

See [PRODUCT_RECOVERY.md](PRODUCT_RECOVERY.md) for the bounded correction policy,
per-product audit/status fields and separate failure metadata. Recoverable product
validation failures now continue after at most one correction; API errors and
source-integrity failures stop processing. Invalid/corrupt checkpoints are fatal
and no longer trigger paid replacement requests. Run the suite above with
`tests.test_relationship_recovery` appended to include the new offline tests.
