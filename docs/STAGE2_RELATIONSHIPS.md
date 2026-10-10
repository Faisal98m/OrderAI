# Experimental Stage 2 relationship interpreter

From `C:\Users\faisa\OneDrive\Desktop\OrderAI`, interpret the most recent
previously generated Stage 1 draft:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.relationship_interpreter "tests\output\visual_pdf\draft_page_1_20261008T193445Z_eb9e154e.json"
```

The first extraction also works by substituting
`draft_page_1_20261008T192518Z_60a0cf81.json`.

Stage 2 reads only that JSON. It makes one text-only Structured Outputs request
per product using its name, description, source_text and ambiguities. It does
not read/rerender the PDF, read the saved image, or call Stage 1 extraction.
It reuses Stage 1's environment-based OpenAI client setup. The default is
`gpt-4o`; choose another Structured Outputs model with `--model` or
`OPENAI_RELATIONSHIP_MODEL`. No new dependencies are needed. Stage 2 disables
SDK retries (`max_retries=0`); failed API requests and grounding failures are
not retried automatically. Stage 1's shared client configuration is unchanged.

Results are separate unique files under
`tests/output/relationship_interpretation/enriched_draft_<timestamp>_<id>.json`.
The original draft is never written. Every enriched draft is marked
`visual_pdf_relationships_v1`, remains `needs_review`, and has
`ready_to_publish: false`. No publishing or output-path options exist.
Production `menu.json`, nonvisual/published drafts and repeated Stage 2 runs
are rejected before API requests. Failed/refused/incomplete interpretations
abort the run without saving a partial enriched draft.

Each product gains `relationship_interpretation`:

- `fixed_components`: exact component names, nullable explicit quantities,
  supporting evidence and ambiguities.
- `option_groups`: required decisions, optional additions, variants or flavours;
  nullable selection limits, known choices, exact `menu_reference` and
  `choices_status` (`known`, `unresolved_reference`, `unknown`).
- `unresolved_relationships` and `ambiguities`: uncertainty for human review.

These are experimental sidecars: the original product ID, name, description,
source text/page, base price, currency, tags, status, ambiguities and original
Stage 1 `option_groups` are unchanged. In particular, consult
`relationship_interpretation.option_groups` for Stage 2's proposals. They do
not become production modifiers or replace Stage 1's observations. Source
metadata remains unchanged. Top-level `relationship_stage` records the input
path, SHA-256, model, time and counts. All original validation issues remain;
the existing price/description validator runs again and Stage 2 review issues
and an unconditional experimental-draft blocker are added.

Referenced ranges remain unresolved with empty choices; this single-product
pass does not look elsewhere for alternatives. Evidence excerpts and choice/
component names must occur in the supplied text. Explicit quantities are
checked conservatively against digits or number words zero through twenty.
Unknown quantity scope or selection/mixing rules stay null and need review.
Price fields are excluded from the response schema so the model cannot write
new prices. Optionality or inclusion that requires the missing visual layout
is left unresolved (for example, a bare 'add' instruction).

The model can still misunderstand associations or omit selections, and text
grounding cannot prove semantic correctness. Stage 2 cannot recover information
that Stage 1 omitted, nor know which choices are available on another page.
Unknown ranges/limits and empty groups despite selection wording are review
issues, not automatically resolved. Every result needs human review.

## Semantic limits and review reporting

Quantities count included units; selection limits count alternatives selected.
Numeric proposals now require explicit selection wording within the grounded
group evidence. The deterministic English grammar recognizes `choose/select/pick`
with `exactly`, `at least`, `at most` or `up to`, digits or number words through
twenty, followed by options, alternatives, choices, flavours or toppings. Bare
`or`, included quantities and number of available alternatives are insufficient.
This intentionally conservative grammar may flag valid wording it cannot parse.

The model sidecar is never rewritten: its proposed min/max values and quantities
remain available for audit. Each item gets `relationship_semantic_validation`
with proposed limits, `effective_limits` and recognized wording. Unsupported or
contradictory proposals have null effective limits and a blocking review finding.
No mixing permission is inferred. Explicit limits do not prove the interpretation
is semantically correct and do not permit publication.

Validation now includes three separate sections:

- `stage1_findings`: an exact copy of the original validation report.
- `stage2_findings`: new semantic/relationship findings and the publication gate.
- `active_review_queue`: deduplicated review occurrences with stable IDs, item/page
  links, indexed relationship paths and links back to every contributing finding.

Exact normalized duplicates and a narrow list of known pipeline message aliases
are consolidated; arbitrary paraphrases are not assumed equivalent. Missing-group
findings remain in the queue as `potentially_addressed` when Stage 2 proposes
groups. Human review must confirm coverage; a proposed group never resolves the
original finding automatically. Unresolved range references remain open with
their exact reference and empty choices. Legacy blocking/warning fields remain
for compatibility and audit; the CLI uses only the deduplicated queue.

The summary shows product/group/reference totals, the number of unsupported or
conflicting numeric limit fields, review issues grouped by product, and the
unconditional publication block. Full detailed JSON still saves to a unique
enriched draft file. Input and previous enriched files are never changed. Counts
depend on the next model response; offline regression tests do not predict it.

Run offline regressions:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_visual_pdf_import tests.test_relationship_interpreter -v
```

Tests use mocked API responses for the three deals, optional additions,
preservation, unresolved references, grounding, failures and publication
blocking. They verify plumbing and safeguards; live model quality requires
running the interpretation command and comparing the saved sidecars to Stage 1.

## Grounding diagnostics

Grounding errors now print a JSON diagnostic to stderr containing product ID,
name and page; the indexed relationship path and component/group/choice name;
the exact model evidence and compared field; original and normalized source
fields; and a named rejection rule. Name and quantity failures also report their
specific check. Only menu data is shown, never client configuration, headers or
API exception bodies. Known environment credentials and token-shaped values
are redacted if echoed into data. Redaction is the exception to exact evidence
reporting.

Matching already ignored case and Unicode whitespace (spaces, tabs, newlines,
nonbreaking spaces). This acceptance policy is unchanged: evidence must remain
a contiguous excerpt within a single source field. Punctuation is preserved.
Even a harmless inserted comma may fail; deleting punctuation broadly could
also turn a decimal into a different quantity or change a product name.
`punctuation_ignored_candidate` is a diagnostic hint only, never permission to
accept the evidence. Paraphrasing, combining noncontiguous excerpts, renaming
choices and inventing quantities remain blocked. The original returned evidence
and source data are not rewritten.

To investigate a live failure, rerun the original draft command above manually.
A failed run exits 1 with these diagnostics and does not save an enriched draft.
To capture stderr for comparison:

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.relationship_interpreter "tests\output\visual_pdf\draft_page_1_20261008T193445Z_eb9e154e.json" 2> "tests\output\stage2_grounding_diagnostics.txt"
```

The prior failed model response was not retained, so its exact offending evidence
cannot be reconstructed. A fresh live response may differ. An offline regression
reproduces a representative Family Special failure using unsupported `2 Cod Sticks`
evidence against `2 Kids Bites or Cod Sticks`, and asserts the improved diagnostics:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_relationship_interpreter.RelationshipTests.test_family_grounding_failure_reports_product_path_and_original_evidence -v
```

## Product identity and serving portions

Stage 2 now distinguishes the sold product from its separately included components.
Fixed components equal to the product name (ignoring case/whitespace) fail with
`self_referential_fixed_component`; they are never silently removed. Existing
name-in-evidence, contiguous-source evidence and explicit quantity checks remain
strict. Component evidence must contain both its name and quantity when present.
A source such as `Atlantic Cod Sticks - 3 Sticks` describes product identity and
portion size, not a separately included component. Original source fields remain
unchanged; portion wording can also be retained in Stage 2 ambiguities. Sauce
explicitly named as a topping and genuine fixed deal elements remain supported.

No production or experimental output fields were added. The smallest future
extension would be an optional Stage 2-only `portion` object with `quantity`,
`unit` and `evidence`, independently grounded against the original source. It
must not become a selection limit or production modifier. That extension is not
implemented here.

V2 relationship checkpoints fingerprint exact messages and the structured schema.
This prompt change and the evidence field description change invalidate all prior
Stage 2 keys. Existing checkpoint files remain available for comparison. Stage 1
vision keys are unchanged and remain reusable for an unchanged PDF/model/prompt.
The resume run makes fresh Stage 2 calls for every extracted product, including
previously completed pages; uncached pages still require vision calls. Keep caching
enabled (omit `--no-cache`) to retain valid vision reuse. No live API calls were
made to test this change.

## Safe failure diagnostics

Multi-page failed page records now include `failure_diagnostics` and print that
same diagnostic in the CLI: page/product identity, processing stage, original
exception class, field paths and named local relationship rule (when available).
Pydantic `ValidationError` remains distinct from `GroundingFailure`. Structured
output parsing, post-parse schema checking, grounding and semantic checking have
separate stage labels. Semantic findings normally generate blocked review issues,
not exceptions; their quantities/limit proposals remain preserved.

Only allowlisted error metadata is reported. Raw input, error bodies, headers,
configuration, arbitrary validator messages and context values are excluded.
Model-level Pydantic validators locate the option group; `failed_field_path`
additionally identifies the affected field where a static local rule permits it.
The standalone Stage 2 CLI also excludes raw Pydantic exception text.

The Supersnacker failure saved in
`incomplete_draft_20261010T134736Z_6ec2d1b03016449f9043f84ece59edc4.json`
does not retain its rejected response or validation details. Its exact failed
field cannot be recovered. A self-referential fixed component would instead raise
`GroundingFailure`. Possible Pydantic failures include malformed JSON/types,
missing/extra fields, nonpositive quantities, invalid enums, inconsistent choice
status/reference/choices, required flags, or reversed selection bounds. They are
not proof of the historical failure's cause.

Current prompt instructions are compatible with the validators. JSON Schema alone
cannot enforce every custom cross-field rule; a model can emit a structurally
permitted combination that Pydantic rejects, such as known choices with a nonnull
menu_reference or an unresolved group with nonempty choices. No prompt/schema
changes were made for diagnostics, so checkpoint keys are unchanged. Failed raw
API responses are not logged or checkpointed; future failures retain sanitized
validation metadata, not the rejected response.
