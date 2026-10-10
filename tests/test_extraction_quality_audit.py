"""Offline audit tests: local PDFs, saved JSON and explicit manual ground truth."""
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from orderai.menu_ingestion import extraction_quality_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "menu.pdf"
        with pymupdf.open() as doc:
            for text in ("Example product", "Example drink"):
                doc.new_page().insert_text((50, 50), text)
            doc.save(self.pdf)
        originals, categories, pages = [], [], []
        for number, name in enumerate(("Example product", "Example drink"), 1):
            item = {"id": f"item{number}", "name": name, "description": None, "price": None,
                "currency": None, "source_text": name, "source_page": number,
                "option_groups": [], "status": "needs_review"}
            originals.append(item)
            pages.append({"source_page": number, "status": "complete", "stage1_draft": {"menu": {
                "categories": [{"name": "Mains", "items": [deepcopy(item)]}]}}})
            enriched = {**deepcopy(item), "relationship_stage_status": "complete",
                "relationship_interpretation": {"fixed_components": [], "option_groups": [], "ambiguities": []}}
            categories.append({"name": "Mains", "items": [enriched],
                               "category_sources": [{"source_page": number, "name": "Mains"}]})
        self.draft = {"draft_version": "visual_pdf_multipage_v2", "completion_status": "complete",
            "menu": {"import_status": "needs_review", "source": {"pdf_sha256": audit.sha(self.pdf), "page_count": 2},
                     "categories": categories}, "pages": pages,
            "validation": {"ready_to_publish": False, "active_review_queue": [
                {"issue_id": "price1", "type": "missing_price", "item_id": "item1", "source_page": 1}]}}
        self.path = self.root / "complete_draft_1.json"
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.draft), encoding="utf-8")

    def generate(self):
        return audit.generate(self.pdf, self.path, output_root=self.root / "audits")

    def write_reviews(self, folder, rows):
        with (folder / "human_review.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=audit.REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def confirmed(self, identity, page, **extra):
        return {"record_type": "product_confirmation", "item_id": identity, "source_page": page,
            "decision": "confirmed", "reviewer": "Human reviewer", "evidence": "Compared with printed PDF label", **extra}

    def test_images_inventory_audit_trail_and_blocking_without_ground_truth(self):
        original = self.path.read_bytes()
        folder, summary = self.generate()
        self.assertEqual((folder / "original_audit.json").read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), original)
        for number in (1, 2):
            pix = pymupdf.Pixmap(folder / f"page_{number:02}.png")
            self.assertGreater(pix.width, 100)
        self.assertEqual(summary["extracted_products"], 2)
        self.assertIsNone(summary["accuracy_metrics"])
        self.assertFalse(summary["ready_to_publish"])
        result_path, result = audit.review(folder)
        self.assertIsNone(result["accuracy_metrics"])
        self.assertFalse(result["ready_to_publish"])
        self.assertTrue(result_path.exists())
        grouped = json.loads((folder / "findings.json").read_text())["grouped_findings"]
        self.assertEqual(grouped["Pricing and currency"][0]["finding"]["issue_id"], "price1")

    def test_latest_complete_selected_and_hash_mismatch_never_silently_falls_back(self):
        newer = self.root / "complete_draft_2.json"
        newer.write_text(json.dumps({**self.draft, "marker": "newest"}))
        os.utime(newer, (self.path.stat().st_mtime + 5,) * 2)
        path, data = audit.load_complete(self.pdf, draft_dir=self.root)
        self.assertEqual(path, newer)
        self.assertEqual(data["marker"], "newest")
        altered = deepcopy(self.draft)
        altered["menu"]["source"]["pdf_sha256"] = "wrong"
        newer.write_text(json.dumps(altered))
        os.utime(newer, (self.path.stat().st_mtime + 5,) * 2)
        with self.assertRaises(audit.AuditError):
            audit.load_complete(self.pdf, draft_dir=self.root)

    def test_lost_groups_and_known_choices_are_comparison_flags_not_truth(self):
        rows = audit.inventory(self.draft)
        rows[0]["stage1_option_groups"] = [{"name": "Choose size", "kind": "required_selection", "choices": [{"name": "Large"}]}]
        findings = audit.quality_findings(rows)
        self.assertIn("stage1_group_not_matched_in_stage2", [f["type"] for f in findings])
        self.assertIn("Stage 1 may itself be incorrect", findings[0]["note"])
        rows[0]["stage2_option_groups"] = [{"name": "Size", "kind": "required_selection", "choices": [{"name": "Small"}]}]
        findings = audit.quality_findings(rows)
        self.assertIn("stage1_choices_not_preserved", [f["type"] for f in findings])

    def test_portion_representation_flags_are_restaurant_independent(self):
        rows = audit.inventory(self.draft)
        rows[0].update(name="Example Pieces", source_text="Example Pieces - 3 Pieces",
                       stage2_fixed_components=[{"name": "Pieces", "quantity": 3}])
        rows[1].update(name="Other Pieces", source_text="Other Pieces - 5 Pieces")
        types = {f["type"] for f in audit.quality_findings(rows)}
        self.assertIn("possible_portion_as_component", types)
        self.assertIn("portion_retained_in_source", types)

    def test_manual_omission_false_positive_category_and_price_are_separate(self):
        original = self.path.read_bytes()
        folder, _ = self.generate()
        self.write_reviews(folder, [self.confirmed("item1", 1, verified_name="Verified product"),
            self.confirmed("item2", 2, decision="false_positive"),
            self.confirmed("item1", 1, record_type="category_correction", verified_category="Sides"),
            self.confirmed("item1", 1, record_type="verified_price", verified_price="2.50", verified_currency="GBP"),
            self.confirmed("", 1, record_type="omission", verified_name="Manually seen product")])
        _, result = audit.review(folder)
        self.assertEqual(result["accuracy_metrics"]["precision_on_manually_reviewed_products"], 0.5)
        self.assertNotIn("recall_on_reviewer_asserted_complete_census", result["accuracy_metrics"])
        self.assertEqual(len(result["manual_records"]), 5)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(result["ready_to_publish"])

    def test_recall_only_with_explicit_complete_page_census(self):
        folder, _ = self.generate()
        rows = [self.confirmed("item1", 1), self.confirmed("item2", 2),
                self.confirmed("", 1, record_type="omission", verified_name="Missed product")]
        for page in (1, 2):
            rows.append(self.confirmed("", page, record_type="page_complete", evidence="All products on this page counted; omissions recorded"))
        self.write_reviews(folder, rows)
        _, result = audit.review(folder)
        self.assertAlmostEqual(result["accuracy_metrics"]["recall_on_reviewer_asserted_complete_census"], 2 / 3)
        self.assertTrue(result["complete_census_asserted"])

    def test_invalid_review_records_rejected_and_original_preserved(self):
        folder, _ = self.generate()
        for row in [self.confirmed("item1", 1, evidence=""), self.confirmed("item1", 2),
                    self.confirmed("item1", 1, record_type="verified_price", verified_price="NaN", verified_currency="GBP"),
                    self.confirmed("item1", 1, record_type="verified_price", verified_price="1", verified_currency=""),
                    self.confirmed("", 1, record_type="omission", verified_name="")]:
            self.write_reviews(folder, [row])
            with self.assertRaises(audit.AuditError):
                audit.review(folder)

    def test_html_and_csv_escape_untrusted_source_strings(self):
        text = '=HYPERLINK("bad")<script>alert(1)</script>'
        self.draft["menu"]["categories"][0]["items"][0]["name"] = text
        self.draft["pages"][0]["stage1_draft"]["menu"]["categories"][0]["items"][0]["name"] = text
        self.save()
        folder, _ = self.generate()
        self.assertNotIn("<script>", (folder / "comparison.html").read_text())
        with (folder / "inventory.csv").open(encoding="utf-8-sig", newline="") as file:
            row = next(csv.DictReader(file))
        self.assertTrue(row["name"].startswith("'="))

    def test_production_and_changed_source_are_rejected(self):
        self.draft["validation"]["ready_to_publish"] = True
        self.save()
        with self.assertRaises(audit.AuditError):
            self.generate()
        self.draft["validation"]["ready_to_publish"] = False
        self.save()
        folder, _ = self.generate()
        self.path.write_text("changed draft")
        with self.assertRaises(audit.AuditError):
            audit.review(folder)

    def test_changed_consolidated_source_and_duplicate_ids_are_rejected(self):
        self.draft["menu"]["categories"][0]["items"][0]["price"] = 100
        with self.assertRaises(audit.AuditError):
            audit.inventory(self.draft)
        self.draft["menu"]["categories"][0]["items"][0]["price"] = None
        self.draft["menu"]["categories"][1]["items"][0]["id"] = "item1"
        with self.assertRaises(audit.AuditError):
            audit.inventory(self.draft)

    def test_unique_outputs_cli_and_zero_network_dependencies(self):
        production = self.root / "menu.json"
        production.write_text("keep this production menu")
        with patch.object(audit, "OUTPUT", self.root / "audits"):
            self.assertEqual(audit.main(["generate", str(self.pdf), "--draft", str(self.path)]), 0)
            self.assertEqual(audit.main(["generate", str(self.pdf), "--draft", str(self.path)]), 0)
        self.assertEqual(len(list((self.root / "audits").iterdir())), 2)
        self.assertEqual(production.read_text(), "keep this production menu")
        source = Path(audit.__file__).read_text()
        self.assertNotIn("import openai", source)
        self.assertNotIn("import relationship_interpreter", source)


if __name__ == "__main__":
    unittest.main()
