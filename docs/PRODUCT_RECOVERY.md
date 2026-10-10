# Product-level Stage 2 recovery

V2 now isolates product validation failures. The standalone Stage 2 CLI retains
its fail-fast behaviour; the multi-page orchestrator opts into `ProductRecovery`
through the existing interpreter's optional product hook. Stage 1 and production
code/schema are unchanged.

## Classification and policy

- `structured_output_invalid`: Pydantic validation of API output failed. Permit
  one correction request, using the unchanged source and sanitized validation
  metadata, then retain the original product for human review.
- `grounding_failure`: deterministic source evidence rejected. The same bounded
  correction policy applies; all grounding rules remain unchanged.
- `semantic_review`: structural and grounding checks pass but relationships or
  limits remain uncertain. Preserve validated proposals; never retry for uncertainty.
- `api_failure`: authentication, rate-limit, network and other SDK errors stop
  ingestion without retries. Refused/missing/incomplete responses instead leave
  the product for review and continue, with no corrective request.
- `source_integrity_failure`: PDF mutation, invalid PDF, checkpoint checksum/key
  mismatch, or cached results failing schema/grounding checks stops ingestion
  before any replacement API request. Unclassified pipeline errors also stop.

Correction feedback excludes rejected proposals entirely: SDK parse failures do
not reliably expose a safe complete proposal. It contains only error locations,
named rules and local product context. It cannot bypass schema, grounding or
semantic checks. Failed proposals are neither accepted nor stored.

## Completion and review

`completion_status=complete` remains for CLI compatibility. The additional
`processing_status=processing_complete` means all pages/products were processed,
including products deferred for review. It does not mean approved or publishable.
Fatal failures give `incomplete` / `processing_incomplete` and mark subsequent
pages unattempted. Every output remains `needs_review`, `ready_to_publish=false`.

Successfully interpreted products have `relationship_stage_status=complete` and
an audit outcome of `valid` or `corrected`. Valid uncertainty is classified as
`semantic_review`, retaining sidecars and review issues. Failed products have
`relationship_stage_status=needs_review`, their original Stage 1 fields and no
accepted Stage 2 proposal. Per-product `relationship_audit` and the separate root
`product_recovery_audit` contain sanitized details. The concise review summary
reports validated/corrected/unresolved counts, semantic-review counts, additional
requests, reused failure records, fatal pipeline failures and publication status.
Validated counts include semantic-review results; these remain unapproved.

## Checkpoints and charges

Existing vision and successful relationship fingerprints remain compatible: they
bind PDF/page/model/prompt/schema and source messages. Successful files are never
overwritten. Corrected results also receive a validated original-key checkpoint,
allowing ordinary subsequent runs to reuse the success. Correction request keys
include exact feedback; separate recovery ledger keys additionally include
`product_recovery_v1` and the correction prompt.

`recovery_<fingerprint>.json` files store sanitized attempt/failure metadata, never
unsafe output. An attempt is reserved before a request; an interrupted or failed
attempt without a successful result is deferred for review on subsequent runs,
preventing silent repeat charges. Human review is required before deliberately
reconsidering such a failure. This includes an interrupted attempt that may not
have reached the API: conserving charges takes priority over automatic completion.
No automatic retry override is provided. `--no-cache` disables this protection
as well as successful reuse and can cause fresh charges; use it deliberately.

Each eligible product costs at most one extra request (maximum 6000 additional
output tokens, plus source/feedback input tokens). Request/token bounds are
visible in the operator summary. A currency estimate is left null because model
prices and actual token counts vary; no unsupported dollar estimate is supplied.
All SDK retries remain disabled via the existing client factory.

## Commands

Normal resume (may make paid requests for uncached products/pages):

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.multipage_pdf_import "tests\fixtures\burger_and_sauce_menu.pdf" --max-pages 4
```

Full relevant offline suite:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_visual_pdf_import tests.test_relationship_interpreter tests.test_multipage_pdf_import tests.test_relationship_recovery -v
```

Limitations: recovery can still fail and requires human review; checkpoint hashes
detect corruption but are not cryptographic authentication against deliberate
local tampering. There is no UI, database, publication path, live extraction test,
or rejected-response storage. API failure stops can retain earlier successful
product checkpoints even if the page's enriched draft was not finalized.
