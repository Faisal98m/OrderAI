"""Stage 2 offline regressions: no vision calls or live OpenAI requests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from orderai.menu_ingestion import relationship_interpreter as stage2
from orderai.menu_ingestion import visual_pdf_import as stage1


def draft_fixture():
    texts = [
        ("Supersnacker", "Chicken cheese fries OR animal fries & your choice of canned drink"),
        ("The Burger Combo Deal", "Choose from our original burger range, add fries, "
         "choose from either strips or wings, choice of canned drink"),
        ("The Family Special Deal", "4 Original Burgers 4 Canned Drinks 2 Fruitshoot "
         "4 Fries 2 Kids Bites or Cod Sticks"),
    ]
    items = [{"id": f"existing_{index}", "name": name, "description": text,
              "source_text": text, "price": None, "currency": None, "tags": [],
              "status": "needs_review", "source_page": 1, "option_groups": [],
              "ambiguities": ["Price not specified"]} for index, (name, text) in enumerate(texts)]
    return {"draft_version": "visual_pdf_v1", "menu": {
        "restaurant": "Burger & Sauce", "currency": None, "currency_symbol": None,
        "source": {"type": "visual_pdf", "path": "unavailable.pdf", "page": 1,
                   "rendered_image": "unavailable.png", "model": "gpt-4o", "dpi": 200},
        "import_status": "needs_review", "categories": [{"name": "Latest Deals",
            "description": None, "source_page": 1, "ambiguities": [], "items": items}],
        "unassociated_option_groups": [], "ambiguities": ["Currency not specified"],
    }, "validation": {"ready_to_publish": False, "item_count": 3, "missing_prices": 3,
        "blocking_issues": [{"type": "human_review_required"}], "warnings": [],
        "ambiguous_items": [{"item": "Page", "reason": "Currency not specified"}]}}


def component(name, evidence, quantity=None, ambiguities=None):
    return {"name": name, "quantity": quantity, "evidence": evidence,
            "ambiguities": ambiguities or []}


def group(name, evidence, choices=None, reference=None, unknown_limits=False):
    return {"name": name, "kind": "required_selection", "required": True,
            "min_selections": None if unknown_limits else 1,
            "max_selections": None if unknown_limits else 1,
            "choices_status": "unresolved_reference" if reference else "known",
            "menu_reference": reference, "choices": choices or [], "evidence": evidence,
            "ambiguities": ["Selection limits and mixed choices are unspecified"] if unknown_limits else []}


def interpretation_fixture():
    results = [
        {"fixed_components": [], "option_groups": [
            group("Fries", "Chicken cheese fries OR animal fries", [
                component("Chicken cheese fries", "Chicken cheese fries"),
                component("animal fries", "animal fries")]),
            group("Drink", "your choice of canned drink", reference="choice of canned drink"),
        ], "unresolved_relationships": ["Available canned drinks are not enumerated"], "ambiguities": []},
        {"fixed_components": [], "option_groups": [
            group("Burger", "Choose from our original burger range", reference="original burger range"),
            group("Strips or wings", "choose from either strips or wings", [
                component("strips", "strips"), component("wings", "wings")]),
            group("Drink", "choice of canned drink", reference="choice of canned drink"),
        ], "unresolved_relationships": ["Add fries does not establish included versus optional "
                                         "from text alone", "Burger and drink ranges unavailable"],
         "ambiguities": []},
        {"fixed_components": [
            component("Original Burgers", "4 Original Burgers", 4),
            component("Canned Drinks", "4 Canned Drinks", 4),
            component("Fruitshoot", "2 Fruitshoot", 2),
            component("Fries", "4 Fries", 4),
        ], "option_groups": [group("Kids Bites or Cod Sticks", "2 Kids Bites or Cod Sticks", [
            component("Kids Bites", "2 Kids Bites or Cod Sticks", ambiguities=["Scope of 2 unclear"]),
            component("Cod Sticks", "2 Kids Bites or Cod Sticks", ambiguities=["Quantity not explicit"]),
        ], unknown_limits=True)], "unresolved_relationships": [
            "Whether 2 applies to both alternatives and mixed choices are allowed is unclear"],
         "ambiguities": []},
    ]
    return [stage2.ProductRelationships.model_validate(result) for result in results]


def mock_client(results):
    client = Mock()
    client.chat.completions.parse.side_effect = [SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(parsed=result, refusal=None))]) for result in results]
    return client


class RelationshipTests(unittest.TestCase):
    def test_supersnacker_group_rules_have_safe_named_diagnostics(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        valid = interpretation_fixture()[0]
        stage2.interpret_product(mock_client([valid]), item, "gpt-4o")
        cases = [
            (0, {"choices": []}, "known_choices_required"),
            (1, {"choices": [component("canned drink", "canned drink")]}, "unresolved_choices_must_be_empty"),
            (1, {"menu_reference": None}, "unresolved_reference_text_required"),
            (0, {"menu_reference": "fries"}, "menu_reference_requires_unresolved_status"),
            (0, {"required": None}, "required_selection_requires_true"),
            (0, {"kind": "optional_extra", "required": True}, "optional_extra_requires_false"),
            (0, {"min_selections": 2, "max_selections": 1}, "selection_bounds_order"),
        ]
        for index, change, rule in cases:
            with self.subTest(rule=rule):
                data = valid.model_dump()
                data["option_groups"][index].update(change)
                with self.assertRaises(ValidationError) as schema_failure:
                    stage2.ProductRelationships.model_validate(data)
                client = Mock()
                client.chat.completions.parse.side_effect = schema_failure.exception
                with self.assertRaises(ValidationError) as caught:
                    stage2.interpret_product(client, item, "gpt-4o")
                self.assertIs(caught.exception, schema_failure.exception)
                diagnostic = stage2.failure_diagnostics(caught.exception)
                self.assertEqual(diagnostic["processing_stage"], "stage2_structured_output_parse")
                self.assertEqual(diagnostic["product_name"], "Supersnacker")
                self.assertEqual(diagnostic["validation_errors"][0]["rule"], rule)
                self.assertEqual(diagnostic["validation_errors"][0]["field_path"], f"option_groups[{index}]")

    def test_schema_diagnostics_omit_raw_input_context_and_credentials(self):
        data = interpretation_fixture()[0].model_dump()
        data["option_groups"][0]["kind"] = "sk-private-model-output-token"
        data["unresolved_relationships"] = "Authorization: Bearer sensitive-config"
        with self.assertRaises(ValidationError) as caught:
            stage2.ProductRelationships.model_validate(data)
        diagnostic = stage2.failure_diagnostics(caught.exception, 1, "stage2_structured_output_parse")
        rendered = json.dumps(diagnostic)
        self.assertNotIn("sk-private", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("sensitive-config", rendered)
        self.assertEqual(diagnostic["validation_errors"][0]["field_path"], "option_groups[0].kind")
        self.assertEqual(diagnostic["validation_errors"][0]["rule"], "literal_error")
        self.assertNotIn("input", rendered)

    def test_post_parse_schema_failure_preserves_validation_error_and_stage(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        invalid = interpretation_fixture()[0].model_dump()
        invalid["fixed_components"] = [component("fries", "fries", 0)]
        parsed = Mock()
        parsed.model_dump.return_value = invalid
        client = mock_client([parsed])
        with self.assertRaises(ValidationError) as caught:
            stage2.interpret_product(client, item, "gpt-4o")
        diagnostic = stage2.failure_diagnostics(caught.exception)
        self.assertEqual(diagnostic["processing_stage"], "stage2_relationship_schema_validation")
        self.assertEqual(diagnostic["validation_errors"][0]["field_path"], "fixed_components[0].quantity")
        self.assertEqual(diagnostic["validation_errors"][0]["rule"], "greater_than_equal")

    def test_supersnacker_self_reference_is_grounding_not_schema_failure(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        result = interpretation_fixture()[0]
        result.fixed_components = [stage2.Component.model_validate(component("Supersnacker", "Supersnacker"))]
        with self.assertRaises(stage2.GroundingFailure) as caught:
            stage2.interpret_product(mock_client([result]), item, "gpt-4o")
        self.assertNotIsInstance(caught.exception, ValidationError)
        diagnostic = stage2.failure_diagnostics(caught.exception)
        self.assertEqual(diagnostic["processing_stage"], "stage2_evidence_grounding")
        self.assertEqual(diagnostic["validation_errors"][0]["rule"], "self_referential_fixed_component")
        self.assertEqual(diagnostic["validation_origin"], "deterministic_evidence_grounding")

    def test_portion_identity_is_preserved_without_a_fixed_component(self):
        draft = draft_fixture()
        item = draft["menu"]["categories"][0]["items"][0]
        item.update(name="Atlantic Cod Sticks", description="3 Sticks",
                    source_text="Atlantic Cod Sticks - 3 Sticks")
        draft["menu"]["categories"][0]["items"] = [item]
        result = stage2.ProductRelationships(fixed_components=[], option_groups=[],
            unresolved_relationships=[], ambiguities=["Portion: 3 Sticks; retained in source_text"])
        client = mock_client([result])
        enriched = stage2.enrich_draft(draft, client, "gpt-4o", Path("draft.json"), "hash")
        saved = enriched["menu"]["categories"][0]["items"][0]
        self.assertEqual(saved["source_text"], "Atlantic Cod Sticks - 3 Sticks")
        self.assertEqual(saved["relationship_interpretation"]["fixed_components"], [])
        self.assertFalse(enriched["validation"]["ready_to_publish"])
        prompt = client.chat.completions.parse.call_args.kwargs["messages"][0]["content"]
        self.assertIn("portion-size field", prompt)
        self.assertIn("BOTH its name", prompt)

    def test_cod_count_only_evidence_and_supported_identity_are_rejected(self):
        item = {"id": "cod", "name": "Atlantic Cod Sticks", "source_page": 3,
                "description": "3 Sticks", "source_text": "Atlantic Cod Sticks - 3 Sticks"}
        for evidence, rule in [("3 Sticks", "component_name_not_in_evidence"),
                               (item["source_text"], "self_referential_fixed_component")]:
            result = stage2.ProductRelationships.model_validate({"fixed_components": [
                component(item["name"], evidence, 3)], "option_groups": [],
                "unresolved_relationships": [], "ambiguities": []})
            with self.assertRaises(stage2.GroundingFailure) as caught:
                stage2.check_evidence(item, result)
            self.assertEqual(caught.exception.details["rule"], rule)

    def test_animal_fries_cannot_be_its_own_included_component(self):
        item = {"id": "fries", "name": "Animal Fries", "source_page": 2,
                "description": None, "source_text": "Animal Fries"}
        result = stage2.ProductRelationships(fixed_components=[], option_groups=[],
                                            unresolved_relationships=[], ambiguities=[])
        stage2.check_evidence(item, result)
        result.fixed_components = [stage2.Component.model_validate(component("ANIMAL   FRIES", "Animal Fries"))]
        with self.assertRaises(stage2.GroundingFailure) as caught:
            stage2.check_evidence(item, result)
        self.assertEqual(caught.exception.details["rule"], "self_referential_fixed_component")

    def test_separate_sauce_and_family_components_remain_supported(self):
        item = {"id": "melt", "name": "The Beef Nacho Melt", "source_page": 2,
                "description": "Topped with classic signature sauce",
                "source_text": "The Beef Nacho Melt Topped with classic signature sauce"}
        result = stage2.ProductRelationships.model_validate({"fixed_components": [
            component("classic signature sauce", "Topped with classic signature sauce")],
            "option_groups": [], "unresolved_relationships": [], "ambiguities": []})
        stage2.check_evidence(item, result)
        stage2.check_evidence(draft_fixture()["menu"]["categories"][0]["items"][2],
                              interpretation_fixture()[2])
        result.fixed_components[0].name = "invented sauce"
        with self.assertRaises(stage2.GroundingFailure):
            stage2.check_evidence(item, result)
        result.fixed_components[0].name = "classic signature sauce"
        result.fixed_components[0].quantity = 2
        with self.assertRaises(stage2.GroundingFailure):
            stage2.check_evidence(item, result)
        result.fixed_components[0].quantity = None
        result.fixed_components[0].evidence = "Includes classic signature sauce"
        with self.assertRaises(stage2.GroundingFailure):
            stage2.check_evidence(item, result)

    def test_family_quantity_is_not_a_selection_limit_and_proposal_is_preserved(self):
        original = draft_fixture()
        results = interpretation_fixture()
        results[2].option_groups[0].max_selections = 2
        enriched = stage2.enrich_draft(original, mock_client(results), "gpt-4o", Path("draft.json"), "hash")
        family = enriched["menu"]["categories"][0]["items"][2]
        self.assertEqual(family["relationship_interpretation"]["option_groups"][0]["max_selections"], 2)
        limits = family["relationship_semantic_validation"]["selection_limits"][0]
        self.assertEqual(limits["proposed_limits"]["max_selections"], 2)
        self.assertIsNone(limits["effective_limits"]["max_selections"])
        self.assertIsNone(limits["effective_limits"]["min_selections"])
        conflicts = [issue for issue in enriched["validation"]["stage2_findings"]
                     if issue["type"] == "quantity_selection_limit_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["item_id"], "existing_2")
        self.assertEqual(conflicts[0]["evidence"], "2 Kids Bites or Cod Sticks")
        self.assertEqual(family["source_text"], original["menu"]["categories"][0]["items"][2]["source_text"])
        self.assertEqual([c["quantity"] for c in family["relationship_interpretation"]["fixed_components"]], [4, 4, 2, 4])
        self.assertFalse(enriched["validation"]["ready_to_publish"])

    def test_explicit_selection_wording_supports_limits_but_not_other_bounds(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        for wording, minimum, maximum in [("Choose exactly 2 options: Kids Bites or Cod Sticks", 2, 2),
                                          ("Select up to two alternatives: Kids Bites or Cod Sticks", None, 2),
                                          ("Pick at least 1 choice: Kids Bites or Cod Sticks", 1, None)]:
            item.update(description=wording, source_text=wording)
            result = interpretation_fixture()[2]
            group = result.option_groups[0]
            group.evidence = wording
            group.min_selections, group.max_selections = minimum, maximum
            findings, limits = stage2.semantic_findings(item, result)
            self.assertEqual(limits[0]["effective_limits"], {"min_selections": minimum, "max_selections": maximum})
            self.assertFalse(any(issue["type"] in {"unsupported_selection_limit", "quantity_selection_limit_conflict",
                                                    "selection_limit_contradicts_source"} for issue in findings))
        result.option_groups[0].max_selections = 3
        result.option_groups[0].evidence = "Choose exactly 2 options: Kids Bites or Cod Sticks"
        findings, limits = stage2.semantic_findings(item, result)
        self.assertTrue(any(issue["type"] == "selection_limit_contradicts_source" for issue in findings))
        self.assertIsNone(limits[0]["effective_limits"]["max_selections"])
        self.assertEqual(result.option_groups[0].max_selections, 3)

    def test_review_sections_preserve_audit_and_consolidate_duplicates(self):
        draft = draft_fixture()
        missing = {"type": "potential_missing_option_groups", "item": "Latest Deals / Supersnacker"}
        ambiguity = {"type": "ambiguous_association_or_value", "item": "Latest Deals / Supersnacker",
                     "source_page": 1, "reason": "Selection wording was extracted but option_groups is empty; review the deal relationships."}
        price = {"type": "missing_price", "item_id": "existing_0", "item": "Supersnacker"}
        draft["validation"]["blocking_issues"] += [missing, missing.copy(), ambiguity, price]
        draft["validation"]["ambiguous_items"] += [ambiguity.copy()]
        repeated = {"item": "Latest Deals / Supersnacker", "source_page": 1,
                    "reason": "Available drink range is not enumerated"}
        draft["validation"]["blocking_issues"].append({"type": "ambiguous_association_or_value", **repeated})
        draft["validation"]["ambiguous_items"].append(repeated)
        results = interpretation_fixture()
        results[0].ambiguities.append("Price not specified")
        snapshot = deepcopy(draft["validation"])
        enriched = stage2.enrich_draft(draft, mock_client(results), "gpt-4o", Path("draft.json"), "hash")
        validation = enriched["validation"]
        self.assertEqual(validation["stage1_findings"], snapshot)
        self.assertEqual(draft["validation"], snapshot)
        queue = validation["active_review_queue"]
        omissions = [issue for issue in queue if issue["type"] == "potential_missing_option_groups"]
        self.assertEqual(len(omissions), 1)
        self.assertEqual(omissions[0]["status"], "potentially_addressed")
        self.assertEqual(omissions[0]["item_id"], "existing_0")
        self.assertEqual(omissions[0]["source_page"], 1)
        self.assertEqual(len(omissions[0]["related_findings"]), 4)
        prices = [issue for issue in queue if issue["type"] == "missing_price" and issue["item_id"] == "existing_0"]
        self.assertEqual(len(prices), 1)
        repeated_queue = [issue for issue in queue if issue["reason"] == repeated["reason"]]
        self.assertEqual(len(repeated_queue), 1)
        self.assertEqual(len(repeated_queue[0]["related_findings"]), 2)
        self.assertEqual(len({issue["issue_id"] for issue in queue}), len(queue))
        again = stage2.enrich_draft(draft, mock_client(interpretation_fixture()), "gpt-4o", Path("draft.json"), "hash")
        self.assertIn(omissions[0]["issue_id"], {i["issue_id"] for i in again["validation"]["active_review_queue"]})

    def test_unresolved_references_are_distinct_active_review_items(self):
        enriched = stage2.enrich_draft(draft_fixture(), mock_client(interpretation_fixture()), "gpt-4o", Path("draft.json"), "hash")
        references = [issue for issue in enriched["validation"]["active_review_queue"] if issue["type"] == "unresolved_reference"]
        self.assertEqual(len(references), 3)
        combo = enriched["menu"]["categories"][0]["items"][1]["relationship_interpretation"]
        for index in (0, 2):
            group = combo["option_groups"][index]
            self.assertEqual(group["choices_status"], "unresolved_reference")
            self.assertEqual(group["choices"], [])
        self.assertEqual(combo["option_groups"][0]["menu_reference"], "original burger range")
        self.assertEqual(combo["option_groups"][2]["menu_reference"], "choice of canned drink")
        self.assertEqual(enriched["relationship_stage"]["unresolved_reference_count"], 3)
        self.assertEqual(enriched["relationship_stage"]["proposed_option_group_count"], 6)
        with patch("builtins.print") as output:
            stage2.print_summary(enriched)
        text = str(output.call_args_list)
        for label in ("Products interpreted: 3", "Proposed option groups: 6", "Unresolved references: 3",
                      "Quantity/selection-limit conflicts:", "Outstanding review issues:", "Publication blocked: YES"):
            self.assertIn(label, text)

    def test_case_and_whitespace_differences_remain_grounded(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        result = interpretation_fixture()[2].model_dump()
        result["fixed_components"][0].update(name="original\tBURGERS",
            evidence=" \n4\u00a0\u00a0ORIGINAL\tburgers\r\n ")
        result["option_groups"][0]["evidence"] = "2\nKIDS bites\tOR\u00a0COD sticks"
        parsed = stage2.ProductRelationships.model_validate(result)
        stage2.check_evidence(item, parsed)
        # Model evidence is not rewritten; only its comparison form is normalized.
        self.assertEqual(parsed.fixed_components[0].evidence, result["fixed_components"][0]["evidence"])
        reference_item = draft_fixture()["menu"]["categories"][0]["items"][1]
        reference = interpretation_fixture()[1]
        reference.option_groups[0].menu_reference = "ORIGINAL\nBURGER\tRANGE"
        stage2.check_evidence(reference_item, reference)

    def test_punctuation_difference_is_diagnostic_only_and_still_rejected(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        result = interpretation_fixture()[2]
        result.fixed_components[0].evidence = "4 Original Burgers,"
        with self.assertRaises(stage2.GroundingFailure) as caught:
            stage2.check_evidence(item, result)
        details = caught.exception.details
        self.assertEqual(details["relationship_path"], "fixed_components[0].evidence")
        self.assertEqual(details["returned_evidence"], "4 Original Burgers,")
        self.assertEqual(details["rule"], "case_whitespace_normalized_excerpt_not_found")
        self.assertTrue(details["source_match_results"]["source_text"]["punctuation_ignored_candidate"])
        self.assertFalse(details["source_match_results"]["source_text"]["case_whitespace_match"])
        # Changed number punctuation is also rejected rather than normalized away.
        item["source_text"] = item["description"] = "1.5 portions of Fries"
        result = stage2.ProductRelationships.model_validate({"fixed_components": [
            component("Fries", "15 portions of Fries", 15)], "option_groups": [],
            "unresolved_relationships": [], "ambiguities": []})
        with self.assertRaises(stage2.GroundingFailure):
            stage2.check_evidence(item, result)

    def test_family_grounding_failure_reports_product_path_and_original_evidence(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        item["id"] = "p1_c1_i3_the_family_special_deal"
        result = interpretation_fixture()[2]
        # A deterministic example, not the unavailable response from the live failure.
        result.option_groups[0].choices[1].evidence = "2 Cod Sticks"
        with self.assertRaises(stage2.GroundingFailure) as caught:
            stage2.interpret_product(mock_client([result]), item, "gpt-4o")
        details = caught.exception.details
        self.assertEqual(details["product_id"], item["id"])
        self.assertEqual(details["product_name"], "The Family Special Deal")
        self.assertEqual(details["relationship_path"], "option_groups[0].choices[1].evidence")
        self.assertEqual(details["relationship_name"], "Cod Sticks")
        self.assertEqual(details["returned_evidence"], "2 Cod Sticks")
        self.assertEqual(details["source_texts"]["source_text"], item["source_text"])
        self.assertIn("no enriched draft saved", str(caught.exception))

    def test_diagnostics_distinguish_group_reference_name_quantity_and_empty_excerpt(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][1]
        for field, text, path in [("evidence", "Choose a beef burger", "option_groups[0].evidence"),
                                 ("menu_reference", "Available drinks menu", "option_groups[0].menu_reference")]:
            result = interpretation_fixture()[1]
            setattr(result.option_groups[0], field, text)
            with self.assertRaises(stage2.GroundingFailure) as caught:
                stage2.check_evidence(item, result)
            self.assertEqual(caught.exception.details["relationship_path"], path)
            self.assertEqual(caught.exception.details["compared_text"], text)
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        for field, value, rule in [("name", "Beef Burgers", "component_name_not_in_evidence"),
                                   ("quantity", 7, "explicit_quantity_token_not_found"),
                                   ("evidence", " \t\n ", "empty_excerpt")]:
            result = interpretation_fixture()[2]
            setattr(result.fixed_components[0], field, value)
            with self.assertRaises(stage2.GroundingFailure) as caught:
                stage2.check_evidence(item, result)
            self.assertEqual(caught.exception.details["rule"], rule)
            self.assertEqual(caught.exception.details["relationship_path"], f"fixed_components[0].{field}")
            if field == "quantity":
                self.assertEqual(caught.exception.details["returned_quantity"], 7)
                self.assertEqual(caught.exception.details["digit_tokens_in_evidence"], ["4"])

    def test_paraphrases_omissions_and_combined_source_fields_still_fail(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        for evidence in ("Four Original Burgers", "4 beef burgers", "4 Burgers",
                         "4 Original Burgers 4 Fries"):
            result = interpretation_fixture()[2]
            result.fixed_components[0].evidence = evidence
            with self.assertRaises(stage2.GroundingFailure):
                stage2.check_evidence(item, result)
        item.update(description="4 Original", source_text="Burgers")
        result = interpretation_fixture()[2]
        with self.assertRaises(stage2.GroundingFailure):
            stage2.check_evidence(item, result)

    def test_grounding_diagnostics_redact_credentials_without_reading_dotenv(self):
        secret = "DistinctCredentialValue-AbCd-12345"
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        item["source_text"] += " " + secret
        result = interpretation_fixture()[2]
        result.fixed_components[0].evidence = "Unsupported " + secret
        with patch.dict(stage2.os.environ, {"OPENAI_API_KEY": secret}):
            with self.assertRaises(stage2.GroundingFailure) as caught:
                stage2.check_evidence(item, result)
        printed = str(caught.exception)
        self.assertNotIn(secret.casefold(), printed.casefold())
        self.assertIn("[REDACTED]", printed)
        self.assertEqual(caught.exception.details["product_id"], item["id"])

    def test_stage2_client_disables_shared_sdk_retry_setting(self):
        client = Mock()
        with patch.object(stage2, "_create_client", return_value=client):
            self.assertIs(stage2.create_client(), client.with_options.return_value)
        client.with_options.assert_called_once_with(max_retries=0)

    def test_cli_grounding_failure_prints_diagnostics_without_retry_or_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            draft = draft_fixture()
            draft["menu"]["categories"][0]["items"][2]["id"] = "p1_c1_i3_the_family_special_deal"
            original = json.dumps(draft).encode()
            path.write_bytes(original)
            results = interpretation_fixture()
            results[2].option_groups[0].choices[1].evidence = "2 Cod Sticks"
            client = mock_client(results)
            with patch.object(stage2, "create_client", return_value=client), \
                    patch.object(stage2, "save_enriched") as save, patch("builtins.print") as output:
                self.assertEqual(stage2.main([str(path)]), 1)
                save.assert_not_called()
            self.assertEqual(client.chat.completions.parse.call_count, 3)
            self.assertEqual(path.read_bytes(), original)
            printed = str(output.call_args_list)
            self.assertIn("p1_c1_i3_the_family_special_deal", printed)
            self.assertIn("option_groups[0].choices[1].evidence", printed)
            self.assertIn("2 Cod Sticks", printed)

    def test_three_deals_text_only_and_immutable_stage1_fields(self):
        original = draft_fixture()
        snapshot = deepcopy(original)
        original["menu"]["categories"][0]["items"][0]["price"] = 8.75
        snapshot["menu"]["categories"][0]["items"][0]["price"] = 8.75
        client = mock_client(interpretation_fixture())
        with patch.object(stage1, "render_page", side_effect=AssertionError("No rendering")), \
                patch.object(stage1, "extract_page", side_effect=AssertionError("No vision")):
            enriched = stage2.enrich_draft(original, client, "gpt-4o", Path("draft.json"), "hash")
        self.assertEqual(original, snapshot)
        self.assertEqual(enriched["menu"]["source"], original["menu"]["source"])
        for old, new in zip(original["menu"]["categories"][0]["items"],
                            enriched["menu"]["categories"][0]["items"]):
            self.assertEqual({key: new[key] for key in old}, old)
        self.assertFalse(enriched["validation"]["ready_to_publish"])
        self.assertEqual(enriched["relationship_stage"]["product_count"], 3)
        self.assertEqual(enriched["relationship_stage"]["input_sha256"], "hash")
        self.assertTrue(all(issue in enriched["validation"]["blocking_issues"]
                            for issue in original["validation"]["blocking_issues"]))
        self.assertEqual(client.chat.completions.parse.call_count, 3)
        for call, item in zip(client.chat.completions.parse.call_args_list,
                              original["menu"]["categories"][0]["items"]):
            self.assertIs(call.kwargs["response_format"], stage2.ProductRelationships)
            request = call.kwargs["messages"][1]["content"]
            self.assertIsInstance(request, str)
            self.assertNotIn("image_url", request)
            self.assertEqual(json.loads(request), {key: item[key] for key in
                                                   ("name", "description", "source_text", "ambiguities")})

    def test_supersnacker_choices_and_unresolved_drink_reference(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        result = stage2.interpret_product(mock_client(interpretation_fixture()[:1]), item, "gpt-4o")
        fries, drink = result.option_groups
        self.assertEqual([choice.name for choice in fries.choices], ["Chicken cheese fries", "animal fries"])
        self.assertTrue(fries.required)
        self.assertIsNone(fries.choices[0].quantity)
        self.assertEqual(drink.choices_status, "unresolved_reference")
        self.assertEqual(drink.choices, [])
        self.assertEqual(drink.menu_reference, "choice of canned drink")

    def test_combo_independent_groups_without_invented_burger_or_drink_choices(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][1]
        result = stage2.interpret_product(mock_client(interpretation_fixture()[1:2]), item, "gpt-4o")
        burger, side, drink = result.option_groups
        self.assertEqual(burger.choices, [])
        self.assertEqual(burger.menu_reference, "original burger range")
        self.assertEqual([choice.name for choice in side.choices], ["strips", "wings"])
        self.assertTrue(all(choice.quantity is None for choice in side.choices))
        self.assertEqual(drink.choices, [])
        self.assertTrue(any("Add fries" in reason for reason in result.unresolved_relationships))

    def test_family_explicit_quantities_and_unknown_selection_rules(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][2]
        result = stage2.interpret_product(mock_client(interpretation_fixture()[2:]), item, "gpt-4o")
        self.assertEqual([(component.name, component.quantity) for component in result.fixed_components],
                         [("Original Burgers", 4), ("Canned Drinks", 4), ("Fruitshoot", 2), ("Fries", 4)])
        children = result.option_groups[0]
        self.assertEqual([choice.name for choice in children.choices], ["Kids Bites", "Cod Sticks"])
        self.assertIsNone(children.min_selections)
        self.assertIsNone(children.max_selections)
        self.assertTrue(all(choice.quantity is None for choice in children.choices))
        self.assertTrue(result.unresolved_relationships)

    def test_optional_addition_and_fixed_component(self):
        item = deepcopy(draft_fixture()["menu"]["categories"][0]["items"][0])
        item.update(description="Includes 1 Fries. Optional extra cheese.", source_text=None)
        result = {"fixed_components": [component("Fries", "1 Fries", 1)],
                  "option_groups": [{**group("Cheese", "Optional extra cheese", [component("cheese", "cheese")]),
                                      "kind": "optional_extra", "required": False, "min_selections": 0}],
                  "unresolved_relationships": [], "ambiguities": []}
        parsed = stage2.ProductRelationships.model_validate(result)
        returned = stage2.interpret_product(mock_client([parsed]), item, "gpt-4o")
        self.assertFalse(returned.option_groups[0].required)
        self.assertEqual(returned.fixed_components[0].quantity, 1)

    def test_rejects_invented_names_quantities_and_evidence(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        for changes in ({"name": "Invented burger"}, {"quantity": 6}, {"evidence": "unavailable text"}):
            result = interpretation_fixture()[0].model_dump()
            result["option_groups"][0]["choices"][0].update(changes)
            with self.assertRaises(stage2.ImportFailure):
                stage2.interpret_product(mock_client([stage2.ProductRelationships.model_validate(result)]),
                                         item, "gpt-4o")

    def test_structured_schema_forbids_resolving_ranges_or_altering_prices(self):
        result = interpretation_fixture()[0].model_dump()
        result["option_groups"][1]["choices"] = [component("Cola", "Cola")]
        with self.assertRaises(ValidationError):
            stage2.ProductRelationships.model_validate(result)
        result["option_groups"][1]["choices_status"] = "known"
        with self.assertRaises(ValidationError):
            stage2.ProductRelationships.model_validate(result)
        result = interpretation_fixture()[0].model_dump()
        result["price"] = 4.5
        with self.assertRaises(ValidationError):
            stage2.ProductRelationships.model_validate(result)

    def test_rejects_production_and_malformed_inputs_before_api(self):
        for change in (lambda d: d.update(draft_version="visual_pdf_relationships_v1"),
                       lambda d: d["validation"].update(ready_to_publish=True),
                       lambda d: d["menu"].update(import_status="published"),
                       lambda d: d["menu"]["source"].update(type="website"),
                       lambda d: d["menu"]["categories"][0]["items"][1].update(id="existing_0")):
            draft = draft_fixture()
            change(draft)
            client = Mock()
            with self.assertRaises(stage2.ImportFailure):
                stage2.enrich_draft(draft, client, "gpt-4o", Path("draft.json"), "hash")
            client.chat.completions.parse.assert_not_called()
        with self.assertRaises(stage2.ImportFailure):
            stage2.load_draft(Path("menu.json"))

    def test_cli_preserves_original_file_and_saves_separate_blocked_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.json"
            content = json.dumps(draft_fixture()).encode()
            path.write_bytes(content)
            loaded, digest = stage2.load_draft(path)
            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            with patch.object(stage2, "create_client", return_value=mock_client(interpretation_fixture())), \
                    patch.object(stage2, "REPOSITORY_ROOT", root), \
                    patch("builtins.print"):
                self.assertEqual(stage2.main([str(path)]), 0)
            self.assertEqual(path.read_bytes(), content)
            outputs = list((root / "tests/output/relationship_interpretation").glob("*.json"))
            self.assertEqual(len(outputs), 1)
            enriched = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertFalse(enriched["validation"]["ready_to_publish"])
            self.assertEqual(enriched["draft_version"], "visual_pdf_relationships_v1")
            self.assertEqual(enriched["relationship_stage"]["input_sha256"], digest)

    def test_refusal_truncation_invalid_json_and_api_failure_save_nothing(self):
        item = draft_fixture()["menu"]["categories"][0]["items"][0]
        for reason, refusal, parsed in [("stop", "refused", None), ("length", None, interpretation_fixture()[0]),
                                         ("stop", None, None)]:
            client = Mock()
            client.chat.completions.parse.return_value = SimpleNamespace(choices=[SimpleNamespace(
                finish_reason=reason, message=SimpleNamespace(parsed=parsed, refusal=refusal))])
            with self.assertRaises(stage2.ImportFailure):
                stage2.interpret_product(client, item, "gpt-4o")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("not json", encoding="utf-8")
            with patch.object(stage2, "create_client") as client, \
                    patch.object(stage2, "save_enriched") as save, patch("builtins.print"):
                self.assertEqual(stage2.main([str(path)]), 1)
                client.assert_not_called()
                save.assert_not_called()
            path.write_text(json.dumps(draft_fixture()), encoding="utf-8")
            client = Mock()
            client.chat.completions.parse.side_effect = stage2.OpenAIError("do not print credentials")
            with patch.object(stage2, "create_client", return_value=client), \
                    patch.object(stage2, "save_enriched") as save, patch("builtins.print") as output:
                self.assertEqual(stage2.main([str(path)]), 1)
                save.assert_not_called()
                self.assertNotIn("do not print credentials", str(output.call_args_list))


if __name__ == "__main__":
    unittest.main()
