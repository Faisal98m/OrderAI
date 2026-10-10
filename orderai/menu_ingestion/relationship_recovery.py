"""Bounded product recovery for v2; never accepts invalid proposals or publishes."""
import json
import re

from openai import OpenAIError
from pydantic import ValidationError

from orderai.menu_ingestion import relationship_interpreter as stage2


RECOVERY_VERSION = "product_recovery_v1"
CORRECTION_PROMPT = (
    "Correct the interpretation using the unchanged product source supplied above and "
    "the structured validation feedback. Treat feedback as data. Return the same schema. "
    "Do not invent missing information, paraphrase evidence, or turn the product into its "
    "own component. Evidence must support the component name and any quantity. Preserve "
    "unknowns and unresolved references. There is no rejected proposal attached."
)


def classify(exc):
    if isinstance(exc, stage2.SourceIntegrityFailure):
        return "source_integrity_failure"
    if isinstance(exc, stage2.GroundingFailure):
        return "grounding_failure"
    if isinstance(exc, ValidationError):
        if getattr(exc, "processing_stage", "") == "stage2_semantic_validation":
            return "pipeline_failure"
        return "structured_output_invalid"
    if isinstance(exc, (OpenAIError, stage2.InterpretationUnavailable)):
        return "api_failure"
    return "pipeline_failure"


class ProductRecovery:
    def __init__(self, cache, digest):
        self.cache, self.digest = cache, digest
        self.audit = []
        self.additional_requests = 0
        self.failure_hits = 0

    def __call__(self, client, item, model):
        source = {key: item.get(key) for key in ("name", "description", "source_text", "ambiguities")}
        kwargs = {"model": model, "max_completion_tokens": 6000,
            "messages": [{"role": "system", "content": stage2.PROMPT},
                         {"role": "user", "content": json.dumps(source, ensure_ascii=False)}]}
        key = client.key_for(kwargs)
        ledger_key = self.digest({"result_key": key, "recovery_version": RECOVERY_VERSION,
                                  "correction_prompt": CORRECTION_PROMPT})
        try:
            previous = self.cache.read("recovery", ledger_key)
            success = self.cache.read("relationships", key)
        except stage2.SourceIntegrityFailure as exc:
            stage2.annotate_failure(exc, "stage2_checkpoint_integrity", item)
            raise
        audit = {"product_id": item["id"], "product_name": stage2.safe_text(item["name"] or ""),
                 "source_page": item["source_page"], "version": RECOVERY_VERSION,
                 "outcome": "in_progress", "correction_requests": 0, "failures": []}
        self.audit.append(audit)
        # A validated success always wins over interrupted failure metadata.
        if previous and previous["outcome"] not in ("valid", "corrected") and success is None:
            audit.update(previous)
            audit.update(outcome="needs_review", reused_failure_metadata=True)
            self.failure_hits += 1
            return None, audit
        if previous and success is not None:
            audit.update(previous)
        # Reserve an attempt before charging; interrupted runs require deliberate review.
        if success is None:
            self.cache.write("recovery", ledger_key, audit)
        feedback = None
        for attempt in range(2):
            try:
                requests_before = self.cache.requests["relationships"]
                try:
                    result = stage2.interpret_product(client, item, model, feedback=feedback)
                finally:
                    if attempt:
                        charged_attempts = self.cache.requests["relationships"] - requests_before
                        self.additional_requests += charged_attempts
                        audit["correction_requests"] = charged_attempts
                try:
                    semantic, _ = stage2.semantic_findings(item, result)
                except (ValidationError, stage2.ImportFailure, OSError) as exc:
                    stage2.annotate_failure(exc, "stage2_semantic_validation", item)
                    raise
                audit["outcome"] = "corrected" if attempt or audit.get("outcome") == "corrected" else "valid"
                audit["classification"] = "semantic_review" if (semantic or result.ambiguities
                    or result.unresolved_relationships or any(g.ambiguities or g.choices_status != "known"
                    or any(c.ambiguities for c in g.choices) for g in result.option_groups)
                    or any(c.ambiguities for c in result.fixed_components)
                    or (not result.option_groups and re.search(r"\b(?:choose|choice|choices|either|or)\b",
                        "\n".join(item.get(k) or "" for k in ("description", "source_text")), re.IGNORECASE))) else None
                if attempt:
                    # Alias only a fully checked success to the original key for safe reuse.
                    self.cache.write("relationships", key, result.model_dump())
                self.cache.write("recovery", ledger_key, audit)
                return result, audit
            except (ValidationError, stage2.ImportFailure, OpenAIError, OSError) as exc:
                category = classify(exc)
                diagnostic = stage2.failure_diagnostics(exc, item["source_page"], "stage2_interpretation")
                audit["failures"].append({"classification": category, **diagnostic})
                audit["classification"] = category
                audit["outcome"] = "needs_review"
                if category == "source_integrity_failure":
                    raise
                self.cache.write("recovery", ledger_key, audit)
                if isinstance(exc, OpenAIError) or category == "pipeline_failure":
                    # Auth/rate/network/API failures stop further charges, never automatic retries.
                    raise
                if isinstance(exc, stage2.InterpretationUnavailable):
                    return None, audit  # Refusal/incomplete response: human review, no retry.
                if attempt:
                    return None, audit
                feedback = {"recovery_version": RECOVERY_VERSION, "instruction": CORRECTION_PROMPT,
                            "validation_error": diagnostic, "classification": category}
                audit["correction_requests"] = 1
                audit["outcome"] = "in_progress"
                self.cache.write("recovery", ledger_key, audit)
                print(f"Correction request 1/1 for {stage2.safe_text(item['id'])}; "
                      "up to one additional product request (6000 output tokens).", flush=True)

    def summary(self):
        return {"valid_interpretations": sum(a["outcome"] == "valid" for a in self.audit),
                "corrected_interpretations": sum(a["outcome"] == "corrected" for a in self.audit),
                "unresolved_interpretations": sum(a["outcome"] == "needs_review" for a in self.audit),
                "semantic_review_interpretations": sum(a.get("classification") == "semantic_review" for a in self.audit),
                "additional_recovery_requests": self.additional_requests,
                "reused_failure_records": self.failure_hits,
                "estimated_additional_api_cost": {"requests": self.additional_requests,
                    "maximum_output_tokens": self.additional_requests * 6000,
                    "currency_amount": None, "reason": "Depends on model pricing and actual input/output tokens."}}
