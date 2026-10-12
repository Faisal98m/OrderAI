"""Offline production configuration and actual WSGI-entry-point checks."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from flask import Flask, session
from orderai.paths import REPOSITORY_ROOT
from orderai.services.production_config import configure_production, initialise_storage, validate_environment


def environment():
    # Non-secret offline fixture values, never usable service credentials.
    return {'ORDERAI_DEMO_ENABLED': '1', 'ORDERAI_MENU_EDITOR_ENABLED': '0',
            'ORDERAI_DEMO_ACCESS_CODE': 'offline-access-code-fixture',
            'FLASK_SECRET_KEY': 'offline-session-fixture-' + 'x' * 32,
            'OPENAI_API_KEY': 'offline-api-fixture', 'RENDER': 'true',
            'RENDER_EXTERNAL_HOSTNAME': 'demo.example'}


class ProductionTests(unittest.TestCase):
    def test_required_environment_fails_without_printing_values(self):
        valid = environment()
        validate_environment(valid)
        for name in ('ORDERAI_DEMO_ENABLED', 'ORDERAI_MENU_EDITOR_ENABLED', 'ORDERAI_DEMO_ACCESS_CODE',
                     'FLASK_SECRET_KEY', 'OPENAI_API_KEY', 'RENDER_EXTERNAL_HOSTNAME'):
            data = dict(valid)
            data.pop(name)
            with self.assertRaises(RuntimeError) as error:
                validate_environment(data)
            self.assertIn(name, str(error.exception))
            for secret in ('OPENAI_API_KEY', 'FLASK_SECRET_KEY', 'ORDERAI_DEMO_ACCESS_CODE'):
                self.assertNotIn(valid[secret], str(error.exception))

    def test_wrong_flags_weak_secrets_and_invalid_host_fail(self):
        for key, value in (('ORDERAI_DEMO_ENABLED', '0'), ('ORDERAI_MENU_EDITOR_ENABLED', '1'),
                           ('FLASK_SECRET_KEY', 'short'), ('ORDERAI_DEMO_ACCESS_CODE', 'short'),
                           ('RENDER_EXTERNAL_HOSTNAME', 'https://demo.example/path')):
            data = environment()
            data[key] = value
            with self.assertRaises(RuntimeError):
                validate_environment(data)

    def test_secure_cookie_headers_origin_scheme_and_trusted_host(self):
        app = Flask('production-test')
        @app.get('/')
        def home():
            session['demo'] = 'test'
            return 'ok'
        @app.get('/healthz')
        def health():
            return 'ok'
        configure_production(app, environment())
        client = app.test_client()
        response = client.get('/', base_url='http://demo.example', headers={
            'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'attacker.example',
            'X-Forwarded-For': '203.0.113.8'})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['Set-Cookie']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(attribute, cookie)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('https://api.openai.com', response.headers['Content-Security-Policy'])
        self.assertIn('Strict-Transport-Security', response.headers)
        self.assertEqual(client.get('/', base_url='http://demo.example').status_code, 403)
        self.assertEqual(client.get('/healthz', base_url='http://demo.example').status_code, 200)
        self.assertEqual(client.get('/', base_url='https://attacker.example').status_code, 400)
        self.assertFalse(app.debug)

    def test_demo_startup_never_initialises_database(self):
        app = Flask('storage-test')
        app.config['DEMO_ENABLED'] = True
        with patch('orderai.services.database.init_db') as init:
            initialise_storage(app)
            init.assert_not_called()
            app.config['DEMO_ENABLED'] = False
            initialise_storage(app)
            init.assert_called_once()

    def test_gunicorn_configuration_single_worker_threads_port_and_logs(self):
        with patch.dict(os.environ, {'PORT': '12345'}):
            settings = runpy.run_path(str(REPOSITORY_ROOT / 'gunicorn.conf.py'))
        self.assertEqual(settings['bind'], '0.0.0.0:12345')
        self.assertEqual(settings['workers'], 1)
        self.assertEqual(settings['worker_class'], 'gthread')
        self.assertEqual(settings['threads'], 4)
        self.assertFalse(settings['preload_app'])
        self.assertFalse(settings['reload'])
        self.assertNotIn('%(r)', settings['access_log_format'])
        server = Mock()
        server.cfg.workers = 1
        server.cfg.worker_class_str = 'gthread'
        server.cfg.threads = 4
        server.cfg.reload = server.cfg.preload_app = False
        settings['on_starting'](server)
        server.cfg.workers = 2
        with self.assertRaises(RuntimeError):
            settings['on_starting'](server)

    def test_actual_production_entrypoint_and_routes_offline(self):
        result = subprocess.run([sys.executable, '-B', str(REPOSITORY_ROOT / 'tests/production_smoke.py')],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Production WSGI smoke: passed', result.stdout)

    def test_entrypoint_rejects_missing_environment_before_client_import(self):
        clean = {key: value for key, value in os.environ.items()
                 if key not in environment() and key not in ('PYTHONPATH', 'PYTHON_DOTENV_DISABLED')}
        result = subprocess.run([sys.executable, '-B', '-c', 'import orderai.services.production'],
            cwd=REPOSITORY_ROOT, env=clean, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe production configuration', result.stderr)
        self.assertNotIn('OpenAIError', result.stderr)

    def test_runtime_manifest_pins_every_dependency_and_excludes_unneeded_fonts(self):
        lines = (REPOSITORY_ROOT / 'requirements.txt').read_text().splitlines()
        pins = [line for line in lines if line and not line.startswith('#')]
        self.assertTrue(all('==' in line for line in pins))
        self.assertTrue(any(line.startswith('gunicorn==') for line in pins))
        self.assertFalse(any(line.startswith('fonttools==') for line in pins))
        self.assertEqual((REPOSITORY_ROOT / '.python-version').read_text().strip(), '3.12.13')


if __name__ == '__main__':
    unittest.main()
