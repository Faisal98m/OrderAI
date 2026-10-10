"""Offline package/entry-point regressions; SDKs and external requests are mocked."""
import contextlib
import importlib
import io
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

from orderai.paths import REPOSITORY_ROOT


class ProjectStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [patch("openai.OpenAI", return_value=Mock()),
                       patch("twilio.rest.Client", return_value=Mock()),
                       patch("dotenv.load_dotenv", return_value=False),
                       patch("requests.sessions.Session.request", side_effect=AssertionError("External requests prohibited"))]
        for item in cls.patches:
            item.start()
        cls.web = importlib.import_module("orderai.services.web")

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.patches):
            item.stop()

    def test_original_flask_routes_and_templates(self):
        expected = {"/", "/chat", "/transcribe", "/realtime-session", "/realtime-tool",
                    "/admin/orders", "/admin/orders-data", "/admin/orders/<int:order_id>/status", "/static/<path:filename>"}
        self.assertEqual({r.rule for r in self.web.app.url_map.iter_rules()}, expected)
        self.assertEqual(Path(self.web.app.root_path), REPOSITORY_ROOT)
        client = self.web.app.test_client()
        for restaurant in ("sanis", "burger_and_sauce"):
            response = client.get("/?restaurant=" + restaurant)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data)
        self.assertEqual(client.get("/?restaurant=invalid").status_code, 404)
        for route in ("/chat", "/realtime-tool", "/transcribe"):
            self.assertEqual(client.post(route, json={}).status_code, 400)
        with patch.object(self.web, "get_all_orders", return_value=[]):
            self.assertEqual(client.get("/admin/orders").status_code, 200)
            self.assertEqual(client.get("/admin/orders-data").json, {"orders": []})

    def test_browser_session_and_restaurant_isolation(self):
        first, second = self.web.app.test_client(), self.web.app.test_client()
        first.get("/?restaurant=burger_and_sauce")
        second.get("/?restaurant=sanis")
        with patch.object(self.web, "run_agent", return_value=("mock reply", "mock response")) as agent:
            self.assertEqual(first.post("/chat", json={"message": "first"}).status_code, 200)
            self.assertEqual(second.post("/chat", json={"message": "second"}).status_code, 200)
        calls = agent.call_args_list
        self.assertEqual(calls[0].args[3], "burger_and_sauce")
        self.assertEqual(calls[1].args[3], "sanis")
        self.assertNotEqual(calls[0].args[4], calls[1].args[4])

    def test_restaurant_files_and_core_session_store(self):
        from orderai.core import restaurant, session_store
        self.assertEqual(restaurant.RESTAURANTS_DIR, REPOSITORY_ROOT / "restaurants")
        for identity in ("sanis", "burger_and_sauce"):
            self.assertIsInstance(restaurant.load_menu(identity), dict)
            self.assertIsInstance(restaurant.load_restaurant_config(identity), dict)
        one = session_store.get_session_state("structure-test-one")
        two = session_store.get_session_state("structure-test-two")
        self.assertIsNot(one, two)
        self.assertIsNot(one["order"], two["order"])

    def test_database_uses_same_relative_location_and_temp_database_functions(self):
        from orderai.services import database
        self.assertEqual(database.DB_PATH, "orderai.db")
        with tempfile.TemporaryDirectory() as folder, patch.object(database, "DB_PATH", str(Path(folder) / "test.db")):
            database.init_db()
            self.assertEqual(database.get_all_orders(), [])
            identity = database.save_order({"status": "submitted", "total": 1.0, "currency": "GBP",
                "items": [{"item_id": "example", "name": "Example", "quantity": 1, "price": 1.0}]})
            self.assertEqual(database.get_all_orders()[0]["id"], identity)
            self.assertEqual(database.update_order_status(identity, "ready")["status"], "updated")

    def test_data_output_and_environment_paths_stay_at_repository_root(self):
        from orderai.menu_ingestion import visual_pdf_import, multipage_pdf_import, extraction_quality_audit, relationship_interpreter
        self.assertEqual(multipage_pdf_import.OUTPUT, REPOSITORY_ROOT / "tests/output/multipage_pdf")
        self.assertEqual(extraction_quality_audit.ROOT, REPOSITORY_ROOT)
        with patch.object(visual_pdf_import, "load_dotenv") as load, \
                patch.dict("os.environ", {"OPENAI_API_KEY": "offline-placeholder"}), \
                patch.object(visual_pdf_import, "OpenAI"):
            visual_pdf_import.create_client()
        load.assert_called_once_with(REPOSITORY_ROOT / ".env")
        with tempfile.TemporaryDirectory() as directory, patch.object(relationship_interpreter, "REPOSITORY_ROOT", Path(directory)):
            saved = relationship_interpreter.save_enriched({"experimental": True})
            self.assertEqual(saved.parent, Path(directory) / "tests/output/relationship_interpretation")

    def test_root_app_and_flask_compatibility_launcher(self):
        with patch("builtins.input", return_value="exit"), contextlib.redirect_stdout(io.StringIO()) as output:
            runpy.run_path(str(REPOSITORY_ROOT / "app.py"), run_name="__main__")
        self.assertEqual(output.getvalue(), "OrderAI Burger House\nType 'exit' to end the conversation.\n\nOrderAI: Thanks! Goodbye.\n")
        self.assertIs(importlib.import_module("web").app, self.web.app)

    def test_manual_tests_import_without_prompts_network_or_publishing(self):
        with patch("builtins.input", side_effect=AssertionError("No input during discovery")), \
                patch("orderai.menu_ingestion.menu_importer.extract_text_from_url", side_effect=AssertionError("No network")), \
                patch("orderai.menu_ingestion.menu_importer.save_menu", side_effect=AssertionError("No publishing")):
            for name in ("test_menu_importer", "test_url_importer", "test_pdf_importer", "test_whatsapp"):
                importlib.import_module("tests." + name)


if __name__ == "__main__":
    unittest.main()
