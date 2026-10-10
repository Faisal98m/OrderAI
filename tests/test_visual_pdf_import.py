"""Offline regression tests; no API calls, publishing, or WhatsApp sends."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf as fitz

from orderai.menu_ingestion import visual_pdf_import as visual
from orderai.menu_ingestion.menu_importer import import_menu_text, validate_imported_menu


def sample():
    return visual.Extraction.model_validate({
        "restaurant": None, "currency": "GBP", "currency_symbol": "£",
        "categories": [{"name": "Mains", "description": None, "source_page": 1,
                        "ambiguities": [], "items": [{
            "name": "Example", "description": None, "price": None, "currency": "GBP",
            "source_text": "Example", "source_page": 1, "ambiguities": [],
            "option_groups": [{"name": "Size", "kind": "variant", "required": True,
                               "min_selections": 1, "max_selections": 1, "source_page": 1,
                               "ambiguities": [], "choices": [{
                "name": "Large", "description": None, "price": 7.5, "currency": "GBP",
                "price_type": "total", "source_page": 1, "ambiguities": [],
            }]}],
        }]}], "unassociated_option_groups": [], "ambiguities": [],
    })


def deal_relationships():
    """Expected relationship fixture from the supplied first-page deal labels.

    This checks the draft pipeline, not the model's factual extraction quality.
    """
    def group(name, alternatives=(), ambiguity=None, one_of=True, quantities=None):
        return {
            "name": name, "kind": "required_selection", "required": True,
            "min_selections": 1 if one_of else None,
            "max_selections": 1 if one_of else None,
            "source_page": 1, "ambiguities": [ambiguity] if ambiguity else [],
            "choices": [{"name": name, "description": (quantities or {}).get(name),
                         "price": None, "currency": None, "price_type": None,
                         "source_page": 1, "ambiguities": []} for name in alternatives],
        }

    def product(name, description, groups):
        return {"name": name, "description": description, "source_text": description,
                "price": None, "currency": None, "source_page": 1,
                "ambiguities": [], "option_groups": groups}

    extraction = sample().model_dump()
    extraction.update(restaurant="Burger & Sauce", currency=None, currency_symbol=None)
    extraction["categories"][0].update(name="Latest Deals", items=[
        product("The Burger Combo Deal",
                "Choose from our original burger range; add regular fries; "
                "choose either 2 strips or 3 wings; choice of canned drink", [
            group("Choose from our original burger range",
                  ambiguity="Original burger range is referenced but not enumerated on this page."),
            group("Strips or wings", ["Strips", "Wings"],
                  quantities={"Strips": "2 strips", "Wings": "3 wings"}),
            group("Choice of canned drink",
                  ambiguity="See soft drinks for full range; available drinks unresolved."),
        ]),
        product("SuperSnacker", "Chicken cheese fries or animal fries & choice of canned drink", [
            group("Fries choice", ["Chicken cheese fries", "Animal fries"]),
            group("Choice of canned drink", ambiguity="Available canned drinks unresolved."),
        ]),
        product("The Family Special Deal",
                "4 Original Burgers; 4 Canned Drinks; 2 Fruitshoot; 4 Fries; "
                "2 Kids Bites or Cod Sticks", [
            group("Kids Bites or Cod Sticks", ["Kids Bites", "Cod Sticks"], one_of=False,
                  ambiguity="Two included units: quantity scope and whether mixed choices "
                            "are permitted require review."),
        ]),
    ])
    return visual.Extraction.model_validate(extraction)


class VisualImportTests(unittest.TestCase):
    def test_deal_relationships_survive_api_parse_and_saved_draft(self):
        client = Mock()
        client.chat.completions.parse.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                parsed=deal_relationships(), refusal=None))])
        extraction = visual.extract_page(client, b"png", 1, "gpt-4o")
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        with tempfile.TemporaryDirectory() as directory:
            path = visual.save_draft(draft, b"png", Path(directory))
            saved = json.loads(path.read_text(encoding="utf-8"))
        combo, snack, family = saved["menu"]["categories"][0]["items"]
        self.assertEqual(saved["validation"]["item_count"], 3)
        self.assertEqual([len(item["option_groups"]) for item in (combo, snack, family)], [3, 2, 1])
        burger, sides, drink = combo["option_groups"]
        self.assertEqual(burger["choices"], [])
        self.assertIn("original burger range", burger["name"])
        self.assertTrue(burger["ambiguities"])
        self.assertEqual(drink["choices"], [])
        self.assertTrue(drink["ambiguities"])
        self.assertEqual([c["name"] for c in sides["choices"]], ["Strips", "Wings"])
        self.assertEqual([c["description"] for c in sides["choices"]], ["2 strips", "3 wings"])
        self.assertEqual(sides["min_selections"], 1)
        self.assertEqual(sides["max_selections"], 1)
        self.assertIn("regular fries", combo["description"])
        self.assertNotIn("regular fries", [g["name"] for g in combo["option_groups"]])
        self.assertEqual([c["name"] for c in snack["option_groups"][0]["choices"]],
                         ["Chicken cheese fries", "Animal fries"])
        self.assertEqual(snack["option_groups"][1]["choices"], [])
        for component in ("4 Original Burgers", "4 Canned Drinks", "2 Fruitshoot", "4 Fries",
                          "2 Kids Bites or Cod Sticks"):
            self.assertIn(component, family["description"])
            self.assertIn(component, family["source_text"])
        children = family["option_groups"][0]
        self.assertEqual([c["name"] for c in children["choices"]], ["Kids Bites", "Cod Sticks"])
        self.assertIsNone(children["min_selections"])
        self.assertIsNone(children["max_selections"])
        self.assertIn("mixed choices", children["ambiguities"][0])
        for item in (combo, snack, family):
            for group in item["option_groups"]:
                self.assertEqual(group["kind"], "required_selection")
                self.assertTrue(group["required"])
                for choice in group["choices"]:
                    self.assertIsNone(choice["price"])
        self.assertFalse(saved["validation"]["ready_to_publish"])
        self.assertIn("schema_incompatibility",
                      {i["type"] for i in saved["validation"]["blocking_issues"]})

    def test_first_extraction_omissions_are_flagged_without_guessing(self):
        extraction = deal_relationships()
        for item in extraction.categories[0].items:
            item.option_groups = []
        # The actual first extraction preserved Family Special only in source_text.
        extraction.categories[0].items[2].description = None
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        omissions = [issue for issue in draft["validation"]["blocking_issues"]
                     if issue["type"] == "potential_missing_option_groups"]
        self.assertEqual(len(omissions), 3)
        self.assertTrue(all(item["option_groups"] == []
                            for item in draft["menu"]["categories"][0]["items"]))
        self.assertTrue(all(item.option_groups == [] for item in extraction.categories[0].items))

    def test_empty_reference_choices_require_review_even_without_model_ambiguity(self):
        extraction = deal_relationships()
        reference = extraction.categories[0].items[0].option_groups[0]
        reference.ambiguities = []
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        self.assertEqual(draft["menu"]["categories"][0]["items"][0]["option_groups"][0]["choices"], [])
        self.assertTrue(any("original burger range" in entry["item"]
                            and "unresolved" in entry["reason"]
                            for entry in draft["validation"]["ambiguous_items"]))

    def test_optional_addition_is_distinct_from_fixed_included_component(self):
        extraction = sample()
        item = extraction.categories[0].items[0]
        item.description = "Includes regular fries. Optional extra cheese +0.50."
        item.source_text = item.description
        group = item.option_groups[0]
        group.name = "Extra cheese"
        group.kind = "optional_extra"
        group.required = False
        group.min_selections = 0
        group.max_selections = 1
        group.choices[0].name = "Cheese"
        group.choices[0].price = 0.5
        group.choices[0].price_type = "additional"
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        saved = draft["menu"]["categories"][0]["items"][0]
        self.assertIn("Includes regular fries", saved["description"])
        self.assertEqual(len(saved["option_groups"]), 1)
        extra = saved["option_groups"][0]
        self.assertFalse(extra["required"])
        self.assertEqual(extra["kind"], "optional_extra")
        self.assertEqual(extra["choices"][0]["price"], 0.5)
        self.assertEqual(extra["choices"][0]["price_type"], "additional")
        # Fixed components alone do not trigger the selection-omission heuristic.
        item.option_groups = []
        item.description = item.source_text = "Includes regular fries and two juice cartons."
        fixed = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        self.assertNotIn("potential_missing_option_groups",
                         {issue["type"] for issue in fixed["validation"]["blocking_issues"]})

    def test_render_and_bad_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            with fitz.open() as doc:
                doc.new_page(width=72, height=72).insert_text((5, 30), "Example")
                doc.save(pdf)
            png = visual.render_page(pdf)
            pix = fitz.Pixmap(png)
            self.assertEqual((pix.width, pix.height), (200, 200))
            for page in (0, -1, 2, True, 1.5):
                with self.assertRaises(visual.ImportFailure):
                    visual.render_page(pdf, page)
            corrupt = Path(directory) / "broken.pdf"
            corrupt.write_bytes(b"not a PDF")
            for path in (corrupt, Path(directory) / "missing.pdf"):
                with self.assertRaises(visual.ImportFailure):
                    visual.render_page(path)
            protected = Path(directory) / "protected.pdf"
            with fitz.open(pdf) as doc:
                doc.save(protected, encryption=fitz.PDF_ENCRYPT_AES_256,
                         owner_pw="owner", user_pw="reader")
            with self.assertRaises(visual.ImportFailure):
                visual.render_page(protected)

    def test_api_image_and_structured_format(self):
        client = Mock()
        client.chat.completions.parse.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                parsed=sample(), refusal=None))])
        result = visual.extract_page(client, b"png", 1, "gpt-4o")
        kwargs = client.chat.completions.parse.call_args.kwargs
        self.assertIs(kwargs["response_format"], visual.Extraction)
        self.assertEqual(kwargs["messages"][1]["content"][1]["image_url"]["url"],
                         "data:image/png;base64,cG5n")
        self.assertEqual(result.categories[0].items[0].price, None)
        for finish, refusal, parsed in [("stop", "refused", None), ("length", None, sample()),
                                         ("stop", None, None)]:
            client.chat.completions.parse.return_value.choices[0] = SimpleNamespace(
                finish_reason=finish, message=SimpleNamespace(parsed=parsed, refusal=refusal))
            with self.assertRaises(visual.ImportFailure):
                visual.extract_page(client, b"png", 1, "gpt-4o")

    def test_preserve_groups_and_block_publication(self):
        extraction = sample()
        item = extraction.categories[0].items[0]
        item.source_page = 2
        item.ambiguities = ["Price association unclear"]
        extraction.unassociated_option_groups = [item.option_groups[0].model_copy(deep=True)]
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        validation = draft["validation"]
        self.assertFalse(validation["ready_to_publish"])
        self.assertEqual(validation["missing_prices"], 1)
        self.assertEqual(validation["category_count"], 1)
        self.assertEqual(validation["item_count"], 1)
        self.assertTrue(validation["ambiguous_items"])
        types = {issue["type"] for issue in validation["blocking_issues"]}
        self.assertTrue({"schema_incompatibility", "incorrect_source_page", "missing_price"} <= types)
        saved_item = draft["menu"]["categories"][0]["items"][0]
        self.assertEqual(saved_item["option_groups"][0]["choices"][0]["price"], 7.5)
        self.assertEqual(saved_item["source_page"], 1)
        self.assertIsNone(saved_item["description"])
        self.assertEqual(len(draft["menu"]["unassociated_option_groups"]), 1)

    def test_save_unique_drafts_and_retain_existing_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            menu = root / "menu.json"
            menu.write_text("existing menu", encoding="utf-8")
            draft = visual.build_draft(sample(), Path("input.pdf"), 1, "gpt-4o")
            first = visual.save_draft(draft, b"png", root / "drafts")
            second = visual.save_draft(draft, b"png", root / "drafts")
            self.assertNotEqual(first, second)
            self.assertEqual(menu.read_text(encoding="utf-8"), "existing menu")
            saved = json.loads(first.read_text(encoding="utf-8"))
            self.assertTrue(Path(saved["menu"]["source"]["rendered_image"]).is_file())

    def test_empty_and_unknown_currency_block(self):
        extraction = sample()
        extraction.categories = []
        extraction.currency = None
        draft = visual.build_draft(extraction, Path("input.pdf"), 1, "gpt-4o")
        types = {issue["type"] for issue in draft["validation"]["blocking_issues"]}
        self.assertTrue({"no_items_found", "missing_menu_currency"} <= types)

    def test_cli_default_page_and_failures(self):
        with patch.object(visual, "render_page", return_value=b"png") as render, \
                patch.object(visual, "create_client"), \
                patch.object(visual, "extract_page", return_value=sample()), \
                patch.object(visual, "save_draft", return_value=Path("draft.json")), \
                patch("builtins.print"):
            self.assertEqual(visual.main(["input.pdf"]), 0)
            render.assert_called_once_with(Path("input.pdf"), 1)
        with patch.object(visual, "render_page", side_effect=visual.ImportFailure("bad PDF")), \
                patch.object(visual, "create_client") as client, patch("builtins.print"):
            self.assertEqual(visual.main(["input.pdf", "--page", "0"]), 1)
            client.assert_not_called()

    def test_existing_text_import_validation(self):
        menu = import_menu_text("MAINS\nFresh food\nExample\nA simple freshly cooked example meal.",
                                "Example Restaurant")
        result = validate_imported_menu(menu)
        self.assertEqual(result["item_count"], 1)
        self.assertEqual(result["missing_prices"], 1)
        self.assertFalse(result["ready_to_publish"])


if __name__ == "__main__":
    unittest.main()
