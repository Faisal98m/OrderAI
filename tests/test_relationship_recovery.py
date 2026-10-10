"""Product recovery regressions with mocked responses; no paid requests."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

from types import SimpleNamespace
from openai import AuthenticationError, RateLimitError, APIConnectionError

from orderai.menu_ingestion import multipage_pdf_import as multi
from orderai.menu_ingestion import relationship_interpreter as stage2
from tests import test_multipage_pdf_import as fixtures
from tests.test_multipage_pdf_import import extraction, empty_relationships, reference_proposal


def schema_error():
    data = reference_proposal().model_dump()
    data["option_groups"][0]["choices"] = [{"name": "Drink", "quantity": None,
                                            "evidence": "Drink", "ambiguities": []}]
    try:
        stage2.ProductRelationships.model_validate(data)
    except stage2.ValidationError as exc:
        return exc


def ungrounded():
    return stage2.ProductRelationships.model_validate({"fixed_components": [
        {"name": "Invented", "quantity": None, "evidence": "Invented sk-unsafe-output",
         "ambiguities": []}], "option_groups": [], "unresolved_relationships": [], "ambiguities": []})


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.MultiPageTests.setUp
    run_mocked = fixtures.MultiPageTests.run_mocked

    def test_valid_first_response_no_recovery(self):
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        self.assertEqual(result["review_summary"]["valid_interpretations"], 2)
        self.assertEqual(result["review_summary"]["additional_recovery_requests"], 0)
        self.assertEqual(result["processing_status"], "processing_complete")
        self.assertFalse(result["validation"]["ready_to_publish"])

    def test_schema_failure_one_valid_correction_and_safe_reuse(self):
        self.client.chat.completions.parse.side_effect = [schema_error(),
            multi.parsed_response(empty_relationships()), multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        audit = result["product_recovery_audit"][0]
        self.assertEqual(audit["outcome"], "corrected")
        self.assertEqual(audit["failures"][0]["classification"], "structured_output_invalid")
        self.assertEqual(result["review_summary"]["additional_recovery_requests"], 1)
        messages = self.client.chat.completions.parse.call_args_list[1].kwargs["messages"]
        feedback = json.loads(messages[2]["content"])
        self.assertIn("validation_error", feedback)
        self.assertNotIn("rejected_proposal", feedback)
        self.assertEqual(len(self.client.chat.completions.parse.call_args_list), 3)
        self.client.reset_mock()
        again, _, _ = self.run_mocked([])
        self.client.chat.completions.parse.assert_not_called()
        self.assertEqual(again["review_summary"]["corrected_interpretations"], 1)
        self.assertEqual(again["review_summary"]["additional_recovery_requests"], 0)

    def test_two_failed_responses_keep_original_and_continue_same_page(self):
        first = extraction(1)
        next_item = deepcopy(first.categories[0].items[0])
        next_item.name, next_item.source_text = "Next product", "Next product"
        first.categories[0].items.append(next_item)
        self.client.chat.completions.parse.side_effect = [schema_error(), schema_error(),
            multi.parsed_response(empty_relationships()), multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([first, extraction(2)])
        item = result["menu"]["categories"][0]["items"][0]
        self.assertEqual(item["relationship_stage_status"], "needs_review")
        self.assertNotIn("relationship_interpretation", item)
        self.assertEqual(item["source_text"], first.categories[0].items[0].source_text)
        self.assertEqual(item["price"], first.categories[0].items[0].price)
        self.assertEqual(result["review_summary"]["valid_interpretations"], 2)
        self.assertEqual(result["review_summary"]["unresolved_interpretations"], 1)
        self.assertEqual(result["processing_status"], "processing_complete")
        self.assertTrue(any(i["type"] == "stage2_product_failure" for i in result["validation"]["active_review_queue"]))
        self.client.reset_mock()
        again, _, _ = self.run_mocked([])
        self.client.chat.completions.parse.assert_not_called()
        self.assertEqual(again["review_summary"]["reused_failure_records"], 1)

    def test_grounding_feedback_keeps_unsafe_output_out_of_checkpoint(self):
        self.client.chat.completions.parse.side_effect = [multi.parsed_response(ungrounded()),
            multi.parsed_response(empty_relationships()), multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        self.assertEqual(result["product_recovery_audit"][0]["failures"][0]["classification"], "grounding_failure")
        self.assertEqual(result["review_summary"]["corrected_interpretations"], 1)
        for path in self.cache.glob("*.json"):
            self.assertNotIn("sk-unsafe-output", path.read_text())
        feedback = self.client.chat.completions.parse.call_args_list[1].kwargs["messages"][2]["content"]
        self.assertNotIn("sk-unsafe-output", feedback)

    def test_semantic_uncertainty_does_not_trigger_correction(self):
        self.client.chat.completions.parse.side_effect = [multi.parsed_response(reference_proposal()),
                                                       multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1, name="Combo", text="Choose Canned Drink"), extraction(2)])
        self.assertEqual(result["review_summary"]["semantic_review_interpretations"], 1)
        self.assertEqual(result["review_summary"]["additional_recovery_requests"], 0)
        self.assertEqual(result["reference_analysis"][0]["choices_status"], "unresolved_reference")

    def test_api_auth_rate_and_connection_errors_stop_without_retry(self):
        request = SimpleNamespace()
        for error in [AuthenticationError("secret raw body", response=SimpleNamespace(status_code=401, request=request, headers={}), body={}),
                      RateLimitError("secret raw body", response=SimpleNamespace(status_code=429, request=request, headers={}), body={}),
                      APIConnectionError(request=request)]:
            with self.subTest(error=type(error).__name__):
                self.cache = self.root / type(error).__name__
                self.client.reset_mock()
                self.client.chat.completions.parse.side_effect = error
                result, _, _ = self.run_mocked([extraction(1)])
                self.assertEqual(self.client.chat.completions.parse.call_count, 1)
                self.assertEqual(result["completion_status"], "incomplete")
                self.assertEqual(result["review_summary"]["additional_recovery_requests"], 0)
                self.assertEqual(result["product_recovery_audit"][0]["classification"], "api_failure")
                self.assertNotIn("secret raw body", json.dumps(result))

    def test_refusal_is_reviewed_without_correction(self):
        response = multi.parsed_response(empty_relationships())
        response.choices[0].message.refusal = "refused"
        self.client.chat.completions.parse.side_effect = [response, multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        self.assertEqual(result["review_summary"]["additional_recovery_requests"], 0)
        self.assertEqual(result["review_summary"]["unresolved_interpretations"], 1)
        self.assertEqual(result["processing_status"], "processing_complete")

    def test_mutation_during_stage2_stops_before_any_correction_or_success_cache(self):
        def mutate(**kwargs):
            self.pdf.write_bytes(b"mutated during Stage 2")
            return multi.parsed_response(empty_relationships())
        self.client.chat.completions.parse.side_effect = mutate
        result, _, _ = self.run_mocked([extraction(1)])
        self.assertEqual(result["pages"][0]["error_type"], "SourceIntegrityFailure")
        self.assertEqual(result["pages"][1]["status"], "not_attempted")
        self.assertEqual(result["review_summary"]["additional_recovery_requests"], 0)
        self.assertEqual(list(self.cache.glob("relationships_*.json")), [])

    def test_checksum_tamper_is_fatal_without_paid_replacement(self):
        self.run_mocked([extraction(1), extraction(2)])
        checkpoint = next(self.cache.glob("relationships_*.json"))
        checkpoint.write_text('{"signature":"wrong","payload":{},"payload_sha256":"wrong"}')
        self.client.reset_mock()
        result, _, _ = self.run_mocked([])
        self.assertEqual(result["completion_status"], "incomplete")
        self.assertEqual(result["request_counts"]["relationships"], 0)
        self.client.chat.completions.parse.assert_not_called()

    def test_interrupted_attempt_metadata_prevents_repeat_charge(self):
        def interrupt(**kwargs):
            raise KeyboardInterrupt()
        self.client.chat.completions.parse.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_mocked([extraction(1)])
        self.client.reset_mock()
        self.client.chat.completions.parse.side_effect = None
        self.client.chat.completions.parse.return_value = multi.parsed_response(empty_relationships())
        result, extract, _ = self.run_mocked([extraction(2)])
        self.assertEqual(self.client.chat.completions.parse.call_count, 1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(result["review_summary"]["reused_failure_records"], 1)
        self.assertEqual(result["review_summary"]["unresolved_interpretations"], 1)

    def test_original_successful_checkpoint_bytes_are_preserved(self):
        self.run_mocked([extraction(1), extraction(2)])
        original = {p.name: p.read_bytes() for p in self.cache.glob("relationships_*.json")}
        self.run_mocked([])
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.cache.glob("relationships_*.json")})


if __name__ == "__main__":
    unittest.main()
