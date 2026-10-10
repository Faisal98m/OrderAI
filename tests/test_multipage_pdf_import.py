"""Offline v2 tests: generated PDFs and mocked vision/text API results only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pymupdf as fitz

from orderai.menu_ingestion import multipage_pdf_import as multi
from orderai.menu_ingestion import relationship_interpreter as stage2
from orderai.menu_ingestion import visual_pdf_import as stage1
from orderai.menu_ingestion.menu_reference_resolver import reference_candidates
from tests.test_visual_pdf_import import sample


def extraction(page, category="Mains", name="Example", text="Example meal", price=None):
    data = sample().model_dump()
    cat = data["categories"][0]
    cat.update(name=category, source_page=page)
    item = cat["items"][0]
    item.update(name=name, description=text, source_text=text, source_page=page,
                price=price, option_groups=[])
    return stage1.Extraction.model_validate(data)


def empty_relationships():
    return stage2.ProductRelationships(fixed_components=[], option_groups=[],
                                      unresolved_relationships=[], ambiguities=[])


def reference_proposal():
    return stage2.ProductRelationships.model_validate({"fixed_components": [], "option_groups": [{
        "name": "Drink", "kind": "required_selection", "required": True,
        "min_selections": None, "max_selections": None, "choices_status": "unresolved_reference",
        "menu_reference": "Canned Drink", "choices": [], "evidence": "Choose Canned Drink",
        "ambiguities": ["Drink range unavailable"]}], "unresolved_relationships": [], "ambiguities": []})


class MultiPageTests(unittest.TestCase):
    def test_pydantic_failure_reports_product_stage_and_field_without_response(self):
        self.run_mocked([extraction(1, name="Supersnacker"), extraction(2)])
        invalid = reference_proposal().model_dump()
        invalid["option_groups"][0]["menu_reference"] = None
        try:
            stage2.ProductRelationships.model_validate(invalid)
        except stage2.ValidationError as exc:
            failure = exc
        self.client.chat.completions.parse.side_effect = failure
        with patch.object(stage2, "PROMPT", stage2.PROMPT + " diagnostic test cache miss"):
            result, extract, render = self.run_mocked([])
        extract.assert_not_called()
        render.assert_not_called()
        page = result["pages"][0]
        self.assertEqual(page["status"], "complete")
        audit = result["product_recovery_audit"][0]
        diagnostic = audit["failures"][0]
        self.assertEqual(diagnostic["source_page"], 1)
        self.assertEqual(diagnostic["product_id"], "p1_c1_i1_supersnacker")
        self.assertEqual(diagnostic["product_name"], "Supersnacker")
        self.assertEqual(diagnostic["exception_class"], "ValidationError")
        self.assertEqual(diagnostic["processing_stage"], "stage2_structured_output_parse")
        self.assertEqual(diagnostic["validation_errors"][0]["rule"], "unresolved_reference_text_required")
        self.assertEqual(result["request_counts"], {"vision": 0, "relationships": 4})
        self.assertEqual(audit["outcome"], "needs_review")
        self.assertEqual(len(audit["failures"]), 2)
        self.assertFalse(result["validation"]["ready_to_publish"])

    def test_relationship_prompt_change_reuses_only_vision_checkpoints(self):
        self.run_mocked([extraction(1), extraction(2)])
        with patch.object(stage2, "PROMPT", stage2.PROMPT + "\nRevised relationship instructions"):
            result, extract, render = self.run_mocked([])
        extract.assert_not_called()
        render.assert_not_called()
        self.assertEqual(result["checkpoint_hits"], {"vision": 2, "relationships": 0})
        self.assertEqual(result["request_counts"], {"vision": 0, "relationships": 2})
        self.assertEqual(result["completion_status"], "complete")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "menu.pdf"
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), "Page one")
            document.new_page().insert_text((30, 30), "Page two")
            document.save(self.pdf)
        self.cache = self.root / "checkpoints"
        self.client = Mock()
        self.client.chat.completions.parse.return_value = multi.parsed_response(empty_relationships())
        self.factory = Mock(return_value=self.client)

    def run_mocked(self, extractions, **kwargs):
        with patch.object(stage1, "extract_page", side_effect=extractions) as extract, \
                patch.object(stage1, "render_page", return_value=b"PNG") as render:
            result = multi.run(self.pdf, client_factory=self.factory,
                               checkpoint_dir=self.cache, **kwargs)
        return result, extract, render

    def test_sequential_pages_provenance_category_consolidation_and_review(self):
        result, extract, render = self.run_mocked([extraction(1), extraction(2, " mains ", name="Second")])
        self.assertEqual([call.args[2] for call in extract.call_args_list], [1, 2])
        self.assertEqual([call.args[1] for call in render.call_args_list], [1, 2])
        self.assertEqual(result["completion_status"], "complete")
        self.assertEqual(len(result["menu"]["categories"]), 1)
        category = result["menu"]["categories"][0]
        self.assertEqual([source["source_page"] for source in category["category_sources"]], [1, 2])
        self.assertEqual([item["source_page"] for item in category["items"]], [1, 2])
        self.assertEqual([item["id"] for item in category["items"]], ["p1_c1_i1_example", "p2_c1_i1_second"])
        self.assertEqual(result["review_summary"]["missing_prices"], 2)
        self.assertFalse(result["validation"]["ready_to_publish"])
        self.assertEqual(result["request_counts"], {"vision": 2, "relationships": 2})
        self.assertEqual(result["menu"]["import_status"], "needs_review")

    def test_possible_duplicates_are_retained_and_collision_aborts(self):
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        self.assertEqual(result["review_summary"]["item_count"], 2)
        self.assertEqual(len(result["possible_duplicates"]), 1)
        pages = deepcopy(result["pages"])
        first = pages[0]["enriched_draft"]["menu"]["categories"][0]["items"][0]
        pages[1]["enriched_draft"]["menu"]["categories"][0]["items"][0]["id"] = first["id"]
        with self.assertRaisesRegex(stage1.ImportFailure, "ID collision"):
            multi.consolidate(pages, self.pdf, "sha", 2)

    def test_unresolved_reference_candidates_do_not_mutate_or_approve(self):
        self.client.chat.completions.parse.side_effect = [multi.parsed_response(reference_proposal()),
                                                         multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1, "Deals", "Combo", "Choose Canned Drink"),
                                        extraction(2, "Canned Drink", "Cola", "Canned cola", 1.5)])
        snapshot = deepcopy(result["menu"])
        references = reference_candidates(result["menu"])
        self.assertEqual(result["menu"], snapshot)
        reference = references[0]
        self.assertEqual(reference["menu_reference"], "Canned Drink")
        self.assertEqual(reference["status"], "unresolved")
        exact = [c for c in reference["candidates"] if c["match_type"] == "exact_label"]
        self.assertEqual(exact[0]["source_page"], 2)
        self.assertEqual(exact[0]["evidence"]["name"], "Canned Drink")
        self.assertTrue(all(c["approved"] is False for c in reference["candidates"]))
        item = result["menu"]["categories"][0]["items"][0]
        group = item["relationship_interpretation"]["option_groups"][0]
        self.assertEqual(group["choices_status"], "unresolved_reference")
        self.assertEqual(group["choices"], [])
        self.assertIsNone(item["price"])
        self.assertEqual(result["review_summary"]["unresolved_references"], 1)
        self.assertTrue(any(i["type"] == "cross_page_reference_review"
                            for i in result["validation"]["active_review_queue"]))
        menu = deepcopy(result["menu"])
        menu["categories"][1]["name"] = "Canned Drinks"
        menu["categories"][1]["category_sources"][0]["name"] = "Canned Drinks"
        self.assertTrue(any(c["match_type"] == "approximate_lexical" for c in reference_candidates(menu)[0]["candidates"]))

    def test_reference_without_candidates_stays_unresolved(self):
        self.client.chat.completions.parse.side_effect = [multi.parsed_response(reference_proposal()),
                                                         multi.parsed_response(empty_relationships())]
        result, _, _ = self.run_mocked([extraction(1, "Deals", "Combo", "Choose Canned Drink"),
                                        extraction(2, "Sides", "Fries", "Fries")])
        self.assertEqual(result["reference_analysis"][0]["candidates"], [])
        self.assertFalse(result["validation"]["ready_to_publish"])

    def test_limits_rejected_before_rendering_or_api(self):
        for limit in (0, -1, 1):
            with patch.object(stage1, "render_page") as render:
                with self.assertRaises(stage1.ImportFailure):
                    multi.run(self.pdf, max_pages=limit, client_factory=self.factory, checkpoint_dir=self.cache)
                render.assert_not_called()
        self.factory.assert_not_called()

    def test_partial_vision_failure_is_incomplete_and_remaining_pages_not_attempted(self):
        with fitz.open() as doc:
            for _ in range(3):
                doc.new_page()
            doc.save(self.root / "three.pdf")
        self.pdf = self.root / "three.pdf"
        result, extract, _ = self.run_mocked([extraction(1), stage2.OpenAIError("credential do not print")])
        self.assertEqual([page["status"] for page in result["pages"]], ["complete", "failed", "not_attempted"])
        self.assertEqual(result["completion_status"], "incomplete")
        self.assertEqual(extract.call_count, 2)
        self.assertNotIn("credential do not print", json.dumps(result))
        self.assertFalse(result["validation"]["ready_to_publish"])
        with patch.object(multi, "OUTPUT", self.root / "output"):
            saved = multi.save_result(result)
        self.assertTrue(saved.name.startswith("incomplete_draft_"))
        self.assertEqual(json.loads(saved.read_text())["completion_status"], "incomplete")

    def test_partial_relationship_failure_preserves_stage1_and_can_resume(self):
        self.client.chat.completions.parse.side_effect = [stage2.OpenAIError("fail")]
        result, extract, _ = self.run_mocked([extraction(1)])
        self.assertEqual(result["pages"][0]["status"], "failed")
        self.assertEqual(result["review_summary"]["item_count"], 1)
        self.assertEqual(result["menu"]["categories"][0]["items"][0]["relationship_stage_status"], "not_completed")
        self.assertEqual(result["pages"][1]["status"], "not_attempted")
        self.client.chat.completions.parse.side_effect = None
        result, extract, render = self.run_mocked([extraction(2)])
        self.assertEqual(result["completion_status"], "complete")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(render.call_count, 1)
        self.assertEqual(result["checkpoint_hits"]["vision"], 1)

    def test_valid_checkpoints_avoid_calls_and_model_changes_invalidate(self):
        first, _, _ = self.run_mocked([extraction(1), extraction(2, name="Second")])
        self.factory.reset_mock()
        second, extract, render = self.run_mocked([])
        extract.assert_not_called()
        render.assert_not_called()
        self.factory.assert_not_called()
        self.assertEqual(second["checkpoint_hits"], {"vision": 2, "relationships": 2})
        self.assertEqual(first["menu"], second["menu"])
        third, extract, render = self.run_mocked([extraction(1), extraction(2)], vision_model="changed-model")
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(render.call_count, 2)
        self.assertEqual(third["checkpoint_hits"]["relationships"], 0)

    def test_changed_pdf_or_prompt_cannot_reuse_vision_results(self):
        self.run_mocked([extraction(1), extraction(2)])
        with patch.object(stage1, "PROMPT", stage1.PROMPT + " Changed instructions"):
            result, extract, _ = self.run_mocked([extraction(1), extraction(2)])
            self.assertEqual(extract.call_count, 2)
            self.assertEqual(result["checkpoint_hits"]["vision"], 0)
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), "Different menu")
            document.save(self.root / "changed.pdf")
        self.pdf = self.root / "changed.pdf"
        result, extract, _ = self.run_mocked([extraction(1)])
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(result["checkpoint_hits"]["vision"], 0)

    def test_pdf_mutation_during_request_is_incomplete_and_not_checkpointed(self):
        def changed_input(*args):
            self.pdf.write_bytes(b"Changed PDF during request")
            return extraction(1)
        with patch.object(stage1, "extract_page", side_effect=changed_input), \
                patch.object(stage1, "render_page", return_value=b"PNG"):
            result = multi.run(self.pdf, client_factory=self.factory, checkpoint_dir=self.cache)
        self.assertEqual(result["completion_status"], "incomplete")
        self.assertEqual(result["pages"][1]["status"], "not_attempted")
        self.assertIn("PDF changed", result["pages"][0]["error"])
        self.assertEqual(list(self.cache.glob("vision_*.json")), [])

    def test_corrupted_and_encrypted_pdf_rejected_before_client(self):
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"not a PDF")
        encrypted = self.root / "encrypted.pdf"
        with fitz.open(self.pdf) as document:
            document.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
        for pdf in (broken, encrypted):
            with self.assertRaises(stage1.ImportFailure):
                multi.run(pdf, client_factory=self.factory, checkpoint_dir=self.cache)
        self.factory.assert_not_called()

    def test_distinct_category_descriptions_and_unassociated_groups_preserved(self):
        first, second = extraction(1), extraction(2)
        first.categories[0].description = "Page one description"
        second.categories[0].description = "Page two description"
        unassociated = sample().categories[0].items[0].option_groups[0]
        first.unassociated_option_groups = [unassociated]
        result, _, _ = self.run_mocked([first, second])
        self.assertEqual(len(result["menu"]["unassociated_option_groups"]), 1)
        category = result["menu"]["categories"][0]
        self.assertIsNone(category["description"])
        self.assertEqual([source["description"] for source in category["category_sources"]],
                         ["Page one description", "Page two description"])
        self.assertTrue(any(issue["type"] == "category_description_disagreement"
                            for issue in result["validation"]["active_review_queue"]))

    def test_corrupt_or_ungrounded_checkpoint_cannot_be_reused(self):
        self.run_mocked([extraction(1), extraction(2)])
        for path in self.cache.glob("relationships_*.json"):
            stored = json.loads(path.read_text())
            stored["payload"]["fixed_components"] = [{"name": "Invented", "quantity": 2,
                "evidence": "Invented", "ambiguities": []}]
            stored["payload_sha256"] = multi.digest(stored["payload"])
            path.write_text(json.dumps(stored))
        result, extract, render = self.run_mocked([])
        self.assertEqual(result["checkpoint_hits"]["relationships"], 0)
        self.assertEqual(result["request_counts"]["relationships"], 0)
        self.assertEqual(result["pages"][0]["error_type"], "SourceIntegrityFailure")
        self.assertEqual(result["completion_status"], "incomplete")
        extract.assert_not_called()
        render.assert_not_called()

    def test_blank_page_and_conflicting_metadata_remain_reviewable(self):
        blank = extraction(1).model_copy(deep=True)
        blank.categories = []
        second = extraction(2)
        second.currency = "USD"
        result, _, _ = self.run_mocked([blank, second])
        self.assertEqual(result["completion_status"], "complete")
        self.assertEqual(result["request_counts"]["relationships"], 1)
        self.assertIsNone(result["menu"]["currency"])
        self.assertTrue(any(i["type"] == "conflicting_menu_metadata" for i in result["validation"]["blocking_issues"]))
        self.assertTrue(any(i["type"] == "no_items_found" for i in result["validation"]["active_review_queue"]))

    def test_cli_never_overwrites_production_and_partial_run_exits_one(self):
        production = self.root / "menu.json"
        production.write_text("existing restaurant menu")
        result, _, _ = self.run_mocked([extraction(1), extraction(2)])
        with patch.object(multi, "OUTPUT", self.root / "output"), patch.object(multi, "run", return_value=result):
            with patch("builtins.print"):
                self.assertEqual(multi.main([str(self.pdf)]), 0)
            result["completion_status"] = "incomplete"
            with patch("builtins.print"):
                self.assertEqual(multi.main([str(self.pdf)]), 1)
        self.assertEqual(production.read_text(), "existing restaurant menu")
        self.assertEqual(len(list((self.root / "output").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
