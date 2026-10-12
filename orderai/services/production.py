"""Render WSGI entry point. Validate secrets before importing any SDK clients."""
import os
from orderai.services.production_config import configure_production, validate_environment

validate_environment(os.environ)
# .env is a local development convenience, never a production secret source.
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from orderai.services.web import app as _app

app = configure_production(_app, os.environ)
