# Menu management v1

From the project directory, activate the existing virtual environment and run:

```powershell
$env:ORDERAI_MENU_EDITOR_ENABLED = '1'
$env:FLASK_SECRET_KEY = '<your own random local session secret>'
python web.py
```

Open http://127.0.0.1:5000/admin/menu. Select Sani's Chicken or Burger & Sauce,
initialise a draft explicitly, edit products, check readiness, and save draft.
Each restaurant keeps its own unsaved edits while switching. Leaving the page
warns if any restaurant has unsaved edits. Missing prices are blockers; missing
descriptions are warnings. Publishing is not implemented.

Drafts are stored at `restaurants/<id>/menu.draft.json`. Initialisation copies the
existing `menu.json` by default. This is intentional: Sani's existing menu is an
imported website menu, while Burger & Sauce currently has the older category-map
schema. Both schemas and all original metadata are preserved. Neither customer
menus nor extraction source files are written.

To initialise from a visual PDF import instead, set the server-side Flask config
`MENU_IMPORT_SOURCES` to a mapping of restaurant IDs to operator-reviewed JSON
paths before initialisation. For example, in a local launcher after importing
`app` from `web`, assign `app.config['MENU_IMPORT_SOURCES'] = {'burger_and_sauce':
Path('tests/output/multipage_pdf/<selected-complete-draft>.json')}`. Choose the
correct restaurant's complete import explicitly; v1 does not guess which global
extraction output belongs to a restaurant. The visual wrapper and its review
blockers remain intact. The editor cannot resolve extraction/relationship review
requirements, so those imports can remain blocked even after all prices are set.

API (all under `/admin/menu-api/<restaurant>`):

- `GET /draft`: draft, category keys, validation, SHA-256 revision.
- `POST /initialise`: create a draft only if none exists.
- `PUT /draft` or `PATCH /draft`: persist an atomic batch of product edits.
- `POST /validate`: check edits without writing.

Writes and validation accept `{revision, updates: [{category, item_id, name,
description, price}]}`. Category keys come from GET. The session CSRF token from
the editor must be sent as `X-Menu-CSRF`. Price may be null to leave it missing;
otherwise it must be finite, non-negative GBP with at most two decimal places.
Duplicate item IDs are blocked. IDs and unedited fields cannot be replaced by a
client. Stale revisions return 409; malformed files return 422 and are not reset.
Writes use the importer's save function on a temporary file followed by replace.
One process serialises saves with a lock; run only one local server process.

The feature is disabled unless `ORDERAI_MENU_EDITOR_ENABLED=1`. Enabled access
requires a loopback peer and local Host, rejects forwarded requests, and protects
mutations with session CSRF tokens and same-origin checks. Do not enable behind
a reverse proxy or public tunnel. Restaurant IDs are an allowlist, not proof of
ownership. Production authentication, restaurant-specific authorisation, audit
logging and multi-worker locking are deferred; implement these before public use.
`FLASK_SECRET_KEY` is environment-configured; when absent the app uses an ephemeral
random secret (sessions reset on restart). Existing order admin authentication is
outside this feature's scope.

Run the offline tests without API calls:

```powershell
python -m unittest tests.test_menu_management -v
```

Tests use temporary copies of the actual restaurant menus and verify published
files remain unchanged. No database migration or new dependency is required.

## Complete Burger & Sauce extraction

The editor supports null category names. It returns a separate `display_name`
(e.g. `Uncategorised — Page 2`) derived from existing product provenance; the
category's original `name: null` remains unchanged on disk. Product saves send
only edited fields, preserving untouched null descriptions and other evidence.
The original visual wrapper, page records, choices, relationships, active review
queue and non-pricing review blockers are retained. The UI explicitly indicates
how many extraction review issues remain.

Burger & Sauce's draft was initialised from the exact existing four-page file:
`tests/output/multipage_pdf/complete_draft_20261010T142547Z_bd01b3de8e99422b92cceaf08321ac56.json`.
It contains 43 products in six groups (3, 12, 13, 3, 4, 8). The old four-product
draft has a timestamped backup beside the new draft. Source and published menu
files are unchanged. All 43 prices and the source currency remain null. GBP is
proposed for price entry only; explicit currency confirmation is required before
publishing, which v1 does not implement. No manual verification is inferred.

The full-extraction regression tests use this existing saved output, without
calling extraction APIs, and exercise nullable groups, all product counts,
blocker retention, evidence-preserving edits, refresh and Sani's isolation.
