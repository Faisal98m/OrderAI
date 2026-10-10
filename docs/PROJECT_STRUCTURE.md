# Project layout and commands

Run commands from the repository root. The existing `.venv` interpreter is used
below; `.venv-1` is preserved and has not been renamed or moved. There was no
active `VIRTUAL_ENV` in the inspection shell.

## Before

```text
OrderAI/
  app.py, web.py
  agent.py, tools.py, restaurant.py, session_store.py
  database.py, whatsapp.py
  menu_importer.py, visual_pdf_import.py, multipage_pdf_import.py
  relationship_interpreter.py, relationship_recovery.py
  menu_reference_resolver.py, extraction_quality_audit.py
  test_*.py (eight files)
  *.md (five ingestion documents)
  tests/test_whatsapp.py
  tests/fixtures/, tests/output/
  restaurants/, templates/, menu.json, orderai.db
  .env, .venv/, .venv-1/
```

## After

```text
OrderAI/
  app.py                         # Existing terminal ordering entry point
  web.py                         # Compatibility Flask launcher
  orderai/
    __init__.py, paths.py
    core/
      __init__.py
      agent.py, tools.py, restaurant.py, session_store.py
    services/
      __init__.py
      database.py, web.py, whatsapp.py
    menu_ingestion/
      __init__.py
      menu_importer.py, visual_pdf_import.py, multipage_pdf_import.py
      relationship_interpreter.py, relationship_recovery.py
      menu_reference_resolver.py, extraction_quality_audit.py
  tests/
    __init__.py
    test_*.py (original nine files plus test_project_structure.py)
    fixtures/, output/
  docs/
    VISUAL_PDF_IMPORT.md, STAGE2_RELATIONSHIPS.md, MULTIPAGE_PDF_IMPORT.md
    PRODUCT_RECOVERY.md, EXTRACTION_QUALITY_AUDIT.md, PROJECT_STRUCTURE.md
  restaurants/, templates/, menu.json, orderai.db
  .env, .venv/, .venv-1/
```

All fourteen implementation files moved to the package directories shown above.
The root `web.py` is a new compatibility launcher for the moved Flask module.
Eight root test scripts moved into `tests`; the existing WhatsApp script stays
there. The five root Markdown documents moved into `docs`.

## Launch and verify

```powershell
.\.venv\Scripts\python.exe app.py
.\.venv\Scripts\python.exe web.py
# Alternative Flask module launcher:
.\.venv\Scripts\python.exe -m orderai.services.web
# Complete offline suite:
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

The root `app.py` was already the interactive terminal application. The Flask
application lives in `web.py`; `python web.py` and `flask --app web run` retain
their existing entry points.

## Ingestion module commands

```powershell
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.visual_pdf_import "tests\fixtures\burger_and_sauce_menu.pdf" --page 1
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.relationship_interpreter "tests\output\visual_pdf\<draft>.json"
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.multipage_pdf_import "tests\fixtures\burger_and_sauce_menu.pdf" --max-pages 4
# Offline audit:
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.extraction_quality_audit generate "tests\fixtures\burger_and_sauce_menu.pdf"
.\.venv\Scripts\python.exe -m orderai.menu_ingestion.extraction_quality_audit review "tests\output\quality_audit\<audit-directory>"
```

The first three commands may make paid requests when checkpoints are unavailable.
They were not run live during the restructure. Existing output directories and
checkpoint locations remain under root `tests/output`; fingerprint inputs,
schemas, prompts and recovery rules are unchanged.

Manual scripts `tests.test_menu_importer`, `tests.test_url_importer`,
`tests.test_pdf_importer` and `tests.test_whatsapp` now have `main` guards so
unittest discovery cannot execute their side effects. Explicitly running them
with `python -m tests.<name>` retains their previous manual behavior, including
network access, output writes, prompts or publishing/message side effects where
originally present. They are not automated offline test cases.

## Paths and preservation

Imports use the `orderai` package. `orderai.paths.REPOSITORY_ROOT` anchors menu
loading, explicit dotenv loading, Flask templates and ingestion/audit output
paths to the same repository root used before the moves. The database path
remains `orderai.db`, relative to the working directory; launch from this root.

Root `menu.json` is retained. Current restaurant loading uses
`restaurants/<restaurant_id>/menu.json`, rather than that root file. Restaurant
menus/configs, templates, fixtures, SQLite data, environment files, generated
drafts, checkpoints and audit artifacts are not migrated or rewritten.

Git ignores environment files, virtual environments including `.venv-1`, SQLite
files, Python caches and `tests/output`. No tracked files were automatically
removed from Git history. Existing user changes remain part of the working tree.

## Verification limits and pre-existing findings

Verification uses offline mocks for OpenAI, Twilio and external requests. It
checks Flask routes/templates, restaurant and browser session isolation,
temporary SQLite operations, entry-point imports, module help and ingestion
regressions. Live voice, transcription, OpenAI and WhatsApp services are not
called.

Sani's saved menu has a list of categories, while `tools.search_menu` expects a
mapping. This pre-existing format mismatch is preserved and needs separate
business-logic work. Its menu/config still load and its Flask home page renders.
The existing `.env` also produced a dotenv parse warning on line 6 during the
baseline import; no secret values were inspected or printed, and it is unchanged.
The existing Flask script defines its order-status route after the blocking
`app.run` call; that ordering was preserved. Import-based route checks see all
nine routes, but they do not establish that route's availability with the original
script launch mode.
