"""Sequential visual PDF ingestion v2; review-only, with resumable intermediates."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pymupdf as fitz
from openai import OpenAIError
from pydantic import ValidationError

from orderai.menu_ingestion import visual_pdf_import as vision
from orderai.menu_ingestion import relationship_interpreter as relationships
from orderai.menu_ingestion.menu_importer import validate_imported_menu
from orderai.menu_ingestion.menu_reference_resolver import reference_candidates, normalize
from orderai.menu_ingestion.relationship_recovery import ProductRecovery, classify
from orderai.paths import REPOSITORY_ROOT


OUTPUT = REPOSITORY_ROOT / "tests/output/multipage_pdf"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def preflight(pdf: Path, max_pages: int) -> tuple[int, str]:
    if type(max_pages) is not int or max_pages < 1:
        raise vision.ImportFailure("--max-pages must be a positive integer.")
    try:
        with fitz.open(pdf) as document:
            if not document.is_pdf or document.needs_pass or document.is_repaired or not document.page_count:
                raise relationships.SourceIntegrityFailure("Supply a valid, unencrypted, unrepaired PDF with pages.")
            count = document.page_count
            if count > max_pages:
                raise vision.ImportFailure(f"PDF has {count} pages; limit is {max_pages}. No API requests made.")
        return count, hashlib.sha256(pdf.read_bytes()).hexdigest()
    except vision.ImportFailure:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise relationships.SourceIntegrityFailure("Cannot open PDF; check its path and integrity.") from exc


class Checkpoints:
    def __init__(self, directory: Path, enabled=True):
        self.directory, self.enabled = directory, enabled
        self.hits = {"vision": 0, "relationships": 0}
        self.requests = {"vision": 0, "relationships": 0}

    def read(self, kind, signature):
        if not self.enabled:
            return None
        path = self.directory / f"{kind}_{signature}.json"
        if not path.exists():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored["signature"] == signature and stored["payload_sha256"] == digest(stored["payload"]):
                return stored["payload"]
        except (OSError, ValueError, KeyError, TypeError):
            raise relationships.SourceIntegrityFailure("Invalid checkpoint integrity; no API request made.") from None
        raise relationships.SourceIntegrityFailure("Checkpoint hash/signature mismatch; no API request made.")

    def write(self, kind, signature, payload):
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{kind}_{signature}.json"
        if kind != "recovery" and path.exists():
            if self.read(kind, signature) != payload:
                raise relationships.SourceIntegrityFailure("Successful checkpoint cannot be overwritten.")
            return
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        temporary.write_text(json.dumps({"signature": signature, "payload": payload,
            "payload_sha256": digest(payload)}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)  # Only fingerprint-named checkpoint files, never menu.json.


def parsed_response(parsed):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(parsed=parsed, refusal=None))])


def verify_pdf_unchanged(pdf, expected_sha):
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != expected_sha:
        raise relationships.SourceIntegrityFailure("PDF changed during ingestion; stop and rerun with the intended file.")


class RelationshipClient:
    """Cache the same text-only Structured Outputs calls used by Stage 2."""
    def __init__(self, checkpoints, pdf_sha, page, stage1_key, factory):
        self.cache, self.pdf_sha, self.page, self.stage1_key = checkpoints, pdf_sha, page, stage1_key
        self.factory, self.client = factory, None
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    def key_for(self, kwargs):
        return digest({"pdf_sha256": self.pdf_sha, "page": self.page,
            "stage1_key": self.stage1_key, "model": kwargs["model"], "messages": kwargs["messages"],
            "schema": relationships.ProductRelationships.model_json_schema(),
            "max_completion_tokens": kwargs["max_completion_tokens"], "version": "relationships_cache_v1"})

    def parse(self, **kwargs):
        if hasattr(self, "integrity_check"):
            self.integrity_check()
        signature = self.key_for(kwargs)
        context = json.loads(kwargs["messages"][1]["content"])
        grounding_item = {**context, "id": "checkpoint_validation", "source_page": self.page}
        payload = self.cache.read("relationships", signature)
        if payload is not None:
            try:
                result = relationships.ProductRelationships.model_validate(payload)
                relationships.check_evidence(grounding_item, result)
                relationships.semantic_findings(grounding_item, result)
                self.cache.hits["relationships"] += 1
                return parsed_response(result)
            except (ValidationError, relationships.GroundingFailure):
                raise relationships.SourceIntegrityFailure("Cached relationship result failed schema/grounding integrity.") from None
        if self.client is None:
            self.client = self.factory()
        self.cache.requests["relationships"] += 1
        response = self.client.chat.completions.parse(**kwargs)
        if hasattr(self, "integrity_check"):
            self.integrity_check()
        if response.choices:
            choice = response.choices[0]
            if choice.finish_reason == "stop" and not choice.message.refusal and choice.message.parsed is not None:
                try:
                    result = relationships.ProductRelationships.model_validate(choice.message.parsed.model_dump())
                except ValidationError as exc:
                    relationships.annotate_failure(exc, "stage2_checkpoint_schema_validation")
                    raise
                # Do not cache before grounding; caller still checks with actual product ID.
                try:
                    relationships.check_evidence(grounding_item, result)
                except vision.ImportFailure:
                    return response
                relationships.semantic_findings(grounding_item, result)
                self.cache.write("relationships", signature, result.model_dump())
        return response


def page_extraction(pdf, page, pdf_sha, model, cache, client_factory):
    verify_pdf_unchanged(pdf, pdf_sha)
    signature = digest({"pdf_sha256": pdf_sha, "page": page, "dpi": 200, "model": model,
        "prompt": vision.PROMPT, "schema": vision.Extraction.model_json_schema(),
        "version": "vision_cache_v1"})
    payload = cache.read("vision", signature)
    if payload is not None:
        try:
            result = vision.Extraction.model_validate(payload)
            cache.hits["vision"] += 1
            return result, signature
        except ValidationError:
            raise relationships.SourceIntegrityFailure("Cached vision result failed schema integrity.") from None
    png = vision.render_page(pdf, page)
    cache.requests["vision"] += 1
    result = vision.extract_page(client_factory(), png, page, model)
    verify_pdf_unchanged(pdf, pdf_sha)
    # Build/validate once before checkpointing; original evidence and review flags survive.
    vision.build_draft(result, pdf, page, model)
    cache.write("vision", signature, result.model_dump())
    return result, signature


def consolidate(pages: list[dict], pdf: Path, pdf_sha: str, expected_pages: int) -> dict:
    categories, seen_ids, names, issues, queue, unassociated = [], set(), {}, [], [], []
    by_label = {}
    metadata = {field: set() for field in ("restaurant", "currency", "currency_symbol")}
    for page in pages:
        draft = page.get("enriched_draft") or page.get("stage1_draft")
        if draft is None:
            continue
        unassociated.extend(deepcopy(draft["menu"].get("unassociated_option_groups", [])))
        for field in metadata:
            if draft["menu"].get(field) is not None:
                metadata[field].add(draft["menu"][field])
        review = draft["validation"].get("active_review_queue", [])
        queue.extend(deepcopy(review))
        if not review:
            for index, issue in enumerate(draft["validation"]["blocking_issues"]):
                queue.append({"issue_id": f"page{page['source_page']}_stage1_{index}",
                    "source_page": page["source_page"], "status": "open", **deepcopy(issue)})
        for index, category in enumerate(draft["menu"]["categories"]):
            label = normalize(category["name"])
            key = label if label else (page["source_page"], index)
            if key not in by_label:
                merged = {"name": category["name"], "description": category["description"],
                          "items": [], "category_sources": []}
                by_label[key] = merged
                categories.append(merged)
            merged = by_label[key]
            merged["category_sources"].append({"source_page": page["source_page"],
                "category_index": index, "name": category["name"],
                "description": category["description"], "ambiguities": deepcopy(category["ambiguities"])})
            if merged["description"] != category["description"]:
                merged["description"] = None
            for item in category["items"]:
                if item["id"] in seen_ids:
                    raise vision.ImportFailure(f"Product ID collision: {item['id']}. Consolidation aborted.")
                seen_ids.add(item["id"])
                copied = deepcopy(item)
                copied.setdefault("relationship_stage_status", "complete" if page.get("enriched_draft") else "not_completed")
                merged["items"].append(copied)
                if normalize(item["name"]):
                    names.setdefault(normalize(item["name"]), []).append({"item_id": item["id"],
                        "product": item["name"], "category": category["name"],
                        "source_page": item["source_page"], "source_text": item["source_text"]})
    duplicates = [members for members in names.values() if len(members) > 1]
    for category in categories:
        descriptions = {source["description"] for source in category["category_sources"]}
        if len(descriptions) > 1:
            issues.append({"type": "category_description_disagreement", "category": category["name"],
                "sources": deepcopy(category["category_sources"]),
                "reason": "Category labels consolidated but differing descriptions require review."})
    for members in duplicates:
        issues.append({"type": "possible_duplicate_products", "items": members,
                       "reason": "Matching product names only; retained separately for human review."})
    for field, values in metadata.items():
        if len(values) > 1:
            issues.append({"type": "conflicting_menu_metadata", "field": field, "values": sorted(values)})
    complete = len(pages) == expected_pages and all(page["status"] == "complete" for page in pages)
    if not complete:
        issues.append({"type": "incomplete_ingestion", "reason": "Some pages failed or were not attempted."})
    issues.append({"type": "multipage_experimental_draft", "reason": "Publication blocked; human review required."})
    menu = {field: next(iter(values)) if len(values) == 1 else None for field, values in metadata.items()}
    menu.update(categories=categories, unassociated_option_groups=unassociated,
        import_status="needs_review", source={"type": "visual_pdf_v2",
        "path": str(pdf.resolve()), "pdf_sha256": pdf_sha, "page_count": expected_pages})
    references = reference_candidates(menu)
    for reference in references:
        issues.append({"type": "cross_page_reference_review", "item_id": reference["item_id"],
            "source_page": reference["source_page"], "reference_id": reference["reference_id"],
            "reason": "Reference candidates are unapproved; original choices remain unresolved."})
    validation = validate_imported_menu(menu)
    validation["blocking_issues"].extend(issues)
    # Page review is retained in full; final queue adds consolidation/reference findings.
    for index, issue in enumerate(issues):
        queue.append({"issue_id": "multi_" + digest(issue)[:20], "status": "open", **deepcopy(issue)})
    unique = {}
    for issue in queue:
        unique.setdefault(issue["issue_id"], issue)
    validation.update(ready_to_publish=False, blocking_issue_count=len(validation["blocking_issues"]),
                      active_review_queue=list(unique.values()))
    return {"draft_version": "visual_pdf_multipage_v2", "completion_status": "complete" if complete else "incomplete",
        "menu": menu, "pages": deepcopy(pages), "possible_duplicates": duplicates,
        "reference_analysis": references, "validation": validation,
        "review_summary": {"page_count": expected_pages, "completed_pages": sum(p["status"] == "complete" for p in pages),
            "failed_pages": [p["source_page"] for p in pages if p["status"] == "failed"],
            "not_attempted_pages": [p["source_page"] for p in pages if p["status"] == "not_attempted"],
            "category_count": len(categories), "item_count": validation["item_count"],
            "missing_prices": validation["missing_prices"], "possible_duplicate_sets": len(duplicates),
            "proposed_option_groups": sum(len(item.get("relationship_interpretation", {}).get("option_groups", []))
                                          for category in categories for item in category["items"]),
            "uninterpreted_products": sum(item["relationship_stage_status"] != "complete"
                                           for category in categories for item in category["items"]),
            "selection_limit_conflicts": sum(page.get("enriched_draft", {}).get("relationship_stage", {}).get(
                "selection_limit_conflict_count", 0) for page in pages),
            "unresolved_references": len(references), "active_review_issues": len(unique),
            "ready_to_publish": False}}


def run(pdf: Path, max_pages=20, vision_model="gpt-4o", relationship_model="gpt-4o", use_cache=True,
        client_factory=None, checkpoint_dir=None) -> dict:
    count, pdf_sha = preflight(pdf, max_pages)  # Reject before any client/API creation.
    factory = client_factory or relationships.create_client  # SDK retries disabled for v2.
    cache = Checkpoints(checkpoint_dir or OUTPUT / "checkpoints", use_cache)
    recovery = ProductRecovery(cache, digest)
    pages = []
    failed = False
    for page in range(1, count + 1):
        record = {"source_page": page, "status": "not_attempted"}
        pages.append(record)
        if failed:
            continue
        print(f"Processing page {page}/{count}", flush=True)
        stage = "stage1_vision_extraction"
        try:
            extraction, key = page_extraction(pdf, page, pdf_sha, vision_model, cache, factory)
            record["extraction"] = extraction.model_dump()
            stage = "stage1_draft_validation"
            draft = vision.build_draft(extraction, pdf, page, vision_model)
            record["stage1_draft"] = draft
            items = [item for category in draft["menu"]["categories"] for item in category["items"]]
            if items:
                stage = "stage2_draft_validation_and_interpretation"
                client = RelationshipClient(cache, pdf_sha, page, key, factory)
                client.integrity_check = lambda: verify_pdf_unchanged(pdf, pdf_sha)
                record["enriched_draft"] = relationships.enrich_draft(draft, client, relationship_model,
                    cache.directory / f"vision_{key}.json", digest(draft), product_interpreter=recovery)
            stage = "pdf_integrity_check"
            verify_pdf_unchanged(pdf, pdf_sha)
            record["status"] = "complete"
        except (vision.ImportFailure, OpenAIError, ValidationError, OSError) as exc:
            failed = True
            record.update(status="failed", error_type=type(exc).__name__,
                error=relationships.safe_text(str(exc)) if isinstance(exc, vision.ImportFailure)
                      else "Page processing failed; no automatic retry. Inspect credentials/model/network or schema.")
            record["failure_diagnostics"] = relationships.failure_diagnostics(exc, page, stage)
            record["failure_diagnostics"]["classification"] = classify(exc)
            print("Page processing failed: " + json.dumps(record["failure_diagnostics"], ensure_ascii=True), flush=True)
    result = consolidate(pages, pdf, pdf_sha, count)
    result["request_counts"] = cache.requests
    result["checkpoint_hits"] = cache.hits
    result["processing_status"] = "processing_complete" if result["completion_status"] == "complete" else "processing_incomplete"
    result["product_recovery_audit"] = recovery.audit
    result["review_summary"].update(recovery.summary())
    result["review_summary"]["fatal_pipeline_failures"] = sum(p["status"] == "failed" for p in pages)
    return result


def save_result(result):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    status = result["completion_status"]
    path = OUTPUT / f"{status}_draft_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2, ensure_ascii=False, allow_nan=False)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--vision-model", default="gpt-4o")
    parser.add_argument("--relationship-model", default="gpt-4o")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.pdf, args.max_pages, args.vision_model, args.relationship_model, not args.no_cache)
        path = save_result(result)
        print(json.dumps(result["review_summary"], indent=2))
        print(f"Completion: {result['completion_status']} | Publication blocked: YES\nSaved review draft: {path}")
        return 0 if result["completion_status"] == "complete" else 1
    except (vision.ImportFailure, ValidationError, OSError) as exc:
        if isinstance(exc, ValidationError):
            print("Multi-page import failed: " + json.dumps(relationships.failure_diagnostics(exc), ensure_ascii=True))
        else:
            print(relationships.safe_text(f"Multi-page import failed: {exc}"))
            if isinstance(exc, relationships.SourceIntegrityFailure):
                diagnostic = relationships.failure_diagnostics(exc, stage="source_preflight_or_consolidation")
                diagnostic["classification"] = "source_integrity_failure"
                print(json.dumps(diagnostic, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
