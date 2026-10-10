# Visual PDF Menu Ingestion v1

Single-page experiment. The existing website importer and ordering code are unchanged.
Uses PyMuPDF to render the selected page at 200 DPI, then sends that PNG to OpenAI
with strict Structured Outputs. PDF text extraction is not used.

From the OrderAI directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.visual_pdf_import "tests\fixtures\burger_and_sauce_menu.pdf" --page 1
```

Omitting `--page` selects page 1. Pages are numbered starting at 1.
Set `OPENAI_API_KEY` in the environment or the project's existing `.env`, exactly
as for `agent.py`. The module repeats that small configuration without importing
`agent.py` and its ordering integrations. Keys are never printed or saved.
The default model is `gpt-4o`; override with `--model` or `OPENAI_VISUAL_PDF_MODEL`
using a model that supports image input and Structured Outputs.

Each successful run creates two unique files in `tests/output/visual_pdf/`:

- `draft_page_1_<UTC timestamp>_<run id>.json`
- `draft_page_1_<UTC timestamp>_<run id>.png`

JSON contains `menu` and `validation`, plus `draft_version`. Every draft has
`import_status: needs_review` and `ready_to_publish: false`. The CLI has no
publish or output-path option, never calls `save_menu`, and never writes any
production `menu.json`. Exit 0 means the draft was saved, not that it passed
review. Invalid inputs, API failures, refusals or incomplete results exit 1.

## Dependencies

No new packages required in the inspected `.venv`: `PyMuPDF`, `openai`,
`python-dotenv`, and `pydantic` (an existing OpenAI SDK dependency) are installed.
The existing importer also needs its existing Flask, requests, BeautifulSoup
and pypdf dependencies. The implementation uses SDK `chat.completions.parse`.
No shared dependency manifest existed; none is introduced for this experiment.

Offline checks:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_visual_pdf_import -v
```

## Schema compatibility and proposed extension

The existing importer uses category lists and flat items. Its validator checks
missing prices/descriptions but does not understand variants, selection limits,
modifier price semantics, currencies or ambiguities. The visual draft reuses
`slugify` and `validate_imported_menu` and adds review blockers separately.
Existing schemas, validators and publishing functions are not modified.

Smallest proposed extension: optional item `option_groups`, with `name`, `kind`
(variant/flavour/required_selection/optional_extra), nullable `required`,
`min_selections`, `max_selections`, and `choices`. Each choice preserves `name`,
`description`, nullable `price`, `currency`, and `price_type` (total/additional/null).
Groups and choices retain `source_page` and `ambiguities`. These fields are
implemented only in the draft; any extracted group produces a
`schema_incompatibility` blocking issue because the current pipeline cannot
validate/use it. Uncertain groups stay in top-level `unassociated_option_groups`
instead of being attached by guesswork. Category/product unknown names and
descriptions stay null. Item IDs are generated locally with page/category/item
indices to avoid duplicate-name collisions.

Before publishing this richer structure, extend validation and review to handle
choice prices/currencies, required selection rules and unresolved associations;
ordering consumers would also need explicit support. The existing root
`menu.json` has a different category mapping shape from importer drafts; this
experiment deliberately targets the importer structure, not that legacy format.

## Limits

Deal relationship instructions distinguish the sold product, fixed included
components, required customer selections, optional additions, and referenced
menu ranges. No schema fields were added: fixed components and their visible
quantities stay in `description` and `source_text`; customer decisions stay in
`option_groups`. Unresolved ranges use the group `name` and `ambiguities` to
preserve the reference, with `choices: []` when alternatives are not available
on the selected page. Illustrative brands/photos are not an exhaustive choice list.
Included unit quantities do not imply selection limits or permission to mix
alternatives; unknown limits stay null and require review.

Validation adds review blockers when selection wording is present but every
option group is missing, or a group has no resolved choices. The wording check
is an English-language review heuristic; it may flag incidental 'or' wording and
does not detect partially missing groups or prove complete model extraction.
It never creates groups or resolves references automatically. Regression tests
use expected relationship fixtures and mocked API results; a fresh live run is
needed to evaluate the model's extraction accuracy with the revised prompt.

One page per invocation; no cross-page relationship recovery, OCR fallback,
dashboard, bulk ingestion or publishing. No coordinates or restaurant labels
are hardcoded. A visual model can still miss or misread content despite the
prompt and structural checks: review the saved PNG against every extracted
item and price. Structural JSON compliance is not evidence of factual accuracy.
Unknown base and choice prices stay null, even if variant prices exist.
Encrypted, damaged/repaired and invalid PDFs are rejected. Large pages may
consume significant memory/API tokens. API extraction requires network access,
an authorized model and incurs the account's API usage charges.
