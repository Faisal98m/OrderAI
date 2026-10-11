"""Local-only draft administration. Customer menu files are never written."""
import hashlib
import ipaddress
import json
import os
import secrets
import tempfile
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session
from orderai.paths import REPOSITORY_ROOT
from orderai.menu_ingestion.menu_importer import save_menu, update_item_price, validate_imported_menu

bp = Blueprint('menu_management', __name__)
RESTAURANTS = {'sanis': "Sani's Chicken", 'burger_and_sauce': 'Burger & Sauce'}
lock = RLock()

class DraftError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def price(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise DraftError('Price must be a non-negative GBP amount with at most two decimal places.')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise InvalidOperation
        result = float(amount)
        if not Decimal(str(result)).is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise DraftError('Price must be finite, non-negative and have at most two decimal places.')

def categories(document):
    if not isinstance(document, dict):
        raise DraftError('Malformed menu structure.', 422)
    menu = document.get('menu', document)
    if not isinstance(menu, dict) or not isinstance(menu.get('categories'), (dict, list)):
        raise DraftError('Malformed menu structure.', 422)
    review = document.get('validation', {})
    if not isinstance(review, dict) or not isinstance(review.get('blocking_issues', []), list) or any(not isinstance(i, dict) for i in review.get('blocking_issues', [])):
        raise DraftError('Malformed extraction validation.', 422)
    raw = menu['categories']
    rows = ([{'key': key, 'name': key.replace('_', ' ').title(), 'items': items}
             for key, items in raw.items()] if isinstance(raw, dict) else
            [{**category, 'key': str(index)} for index, category in enumerate(raw) if isinstance(category, dict)])
    if len(rows) != len(raw):
        raise DraftError('Malformed category.', 422)
    for category in rows:
        if (category.get('name') is not None and not isinstance(category['name'], str)) or not isinstance(category.get('items'), list):
            raise DraftError('Malformed category.', 422)
        for item in category['items']:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or not isinstance(item.get('name'), str):
                raise DraftError('Malformed product.', 422)
            if item.get('description') is not None and not isinstance(item['description'], str):
                raise DraftError('Malformed product description.', 422)
        # Response-only labels: never assign these to the source category name.
        pages = sorted({item['source_page'] for item in category['items']
                        if isinstance(item.get('source_page'), int) and not isinstance(item['source_page'], bool)})
        fallback = ('Uncategorised — Page ' + ', '.join(map(str, pages)) if pages
                    else 'Uncategorised — Group ' + category['key'])
        category['display_name'] = category.get('name') or fallback
    return menu, rows

def validation(document):
    menu, rows = categories(document)
    result = validate_imported_menu({**menu, 'categories': [{**row, 'name': row['display_name']} for row in rows]})
    ids = [item['id'] for row in rows for item in row['items']]
    for row in rows:
        for item in row['items']:
            kind = None
            try:
                price(item.get('price'))
            except DraftError:
                kind = 'invalid_price'
            if not item['name'].strip():
                kind = 'missing_name'
            if ids.count(item['id']) > 1:
                kind = 'ambiguous_item_id'
            if kind:
                result['blocking_issues'].append({'type': kind, 'item_id': item['id'], 'item': item['name'], 'category': row['display_name']})
    # Extraction review requirements survive price/name edits; v1 cannot resolve them.
    result['blocking_issues'].extend(issue for issue in document.get('validation', {}).get('blocking_issues', [])
                                    if issue.get('type') not in {'missing_price', 'no_items_found', 'invalid_price', 'missing_name', 'ambiguous_item_id', 'unsupported_currency'})
    if menu.get('currency') != 'GBP':
        result['blocking_issues'].append({'type': 'unsupported_currency', 'message': 'GBP is proposed for price entry. Currency is unconfirmed; explicit confirmation is required before publishing.'})
    result['extraction_review_issue_count'] = len(document.get('validation', {}).get('active_review_queue', []))
    result['blocking_issue_count'] = len(result['blocking_issues'])
    result['ready_to_publish'] = not result['blocking_issues']
    return result

def directory(restaurant):
    if restaurant not in RESTAURANTS:
        raise DraftError('Unknown restaurant.', 404)
    root = Path(current_app.config.get('MENU_RESTAURANTS_DIR', REPOSITORY_ROOT / 'restaurants')).resolve()
    target = (root / restaurant).resolve()
    if target.parent != root:
        raise DraftError('Unsafe restaurant directory.', 400)
    return target

def read(path):
    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        categories(doc)
        return doc, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        raise DraftError('No draft exists. Initialise a draft to start editing.', 404)
    except (ValueError, TypeError, AttributeError, KeyError):
        raise DraftError('The menu file is malformed. Restore or repair it before editing.', 422)

def draft_path(restaurant):
    folder = directory(restaurant)
    path = folder / 'menu.draft.json'
    if path.is_symlink() or path.resolve().parent != folder:
        raise DraftError('Unsafe draft path.')
    return path

def atomic_save(document, path):
    fd, temporary = tempfile.mkstemp(prefix='.menu-draft-', suffix='.json', dir=path.parent)
    os.close(fd)
    try:
        save_menu(document, temporary)
        with open(temporary, 'r+b') as file:
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def response(document, revision):
    menu, rows = categories(document)
    return jsonify(restaurant=menu.get('restaurant'), categories=rows, revision=revision,
                   currency=menu.get('currency'), validation=validation(document))

def updates(document, entries):
    if not isinstance(entries, list):
        raise DraftError('An updates array is required.')
    _, rows = categories(document)
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'category', 'item_id', 'name', 'description', 'price'}:
            raise DraftError('Invalid product update.')
        matches = [(row, item) for row in rows for item in row['items'] if item['id'] == entry.get('item_id')]
        if len(matches) != 1:
            raise DraftError('Product ID is missing or ambiguous.', 409)
        row, item = matches[0]
        if row['key'] != entry.get('category') or item['id'] in seen:
            raise DraftError('Product category is incorrect or update is duplicated.', 409)
        seen.add(item['id'])
        for field in ('name', 'description'):
            if field in entry:
                value = entry[field]
                if not isinstance(value, str) or len(value) > (300 if field == 'name' else 5000) or (field == 'name' and not value.strip()):
                    raise DraftError('Enter a valid product name and description.')
                item[field] = value.strip()
        if 'price' in entry:
            value = price(entry['price'])
            if value is None:
                item['price'] = None
            else:
                update_item_price({'categories': rows}, item['id'], value)
    return document

@bp.before_request
def local_guard():
    if not current_app.config.get('MENU_EDITOR_ENABLED', False):
        raise DraftError('Menu editor is disabled.', 404)
    try:
        local = ipaddress.ip_address(request.remote_addr or '').is_loopback
    except ValueError:
        local = False
    if not local or urlsplit(request.host_url).hostname not in {'localhost', '127.0.0.1', '::1'} or request.headers.get('X-Forwarded-For') or request.headers.get('Forwarded'):
        raise DraftError('Menu editing is available only on this computer.', 403)
    if request.method != 'GET':
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            raise DraftError('Invalid request origin.', 403)
        expected = session.get('menu_csrf')
        if not expected or not secrets.compare_digest(request.headers.get('X-Menu-CSRF', ''), expected):
            raise DraftError('Reload the editor before saving.', 403)

@bp.errorhandler(DraftError)
def error(exc):
    return jsonify(error=exc.message), exc.status

@bp.errorhandler(OSError)
def storage_error(exc):
    current_app.logger.exception('Menu draft storage failed')
    return jsonify(error='Could not read or save the draft. Check file permissions and retry.'), 500

@bp.get('/admin/menu')
def editor():
    session.setdefault('menu_csrf', secrets.token_urlsafe(32))
    return render_template('menu_management.html', csrf=session['menu_csrf'], restaurants=RESTAURANTS)

@bp.get('/admin/menu-api/<restaurant>/draft')
def get_draft(restaurant):
    return response(*read(draft_path(restaurant)))

@bp.post('/admin/menu-api/<restaurant>/initialise')
def initialise(restaurant):
    with lock:
        path = draft_path(restaurant)
        if path.exists():
            raise DraftError('A draft already exists; it will not be overwritten.', 409)
        # Optional imported source is configured on the server, never supplied by a client.
        source = current_app.config.get('MENU_IMPORT_SOURCES', {}).get(restaurant, directory(restaurant) / 'menu.json')
        doc, _ = read(Path(source))
        atomic_save(doc, path)
        return response(*read(path))

def edited(restaurant):
    path = draft_path(restaurant)
    doc, revision = read(path)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {'revision', 'updates'}:
        raise DraftError('Supply revision and updates as JSON.')
    if body.get('revision') != revision:
        raise DraftError('Draft changed in another editor. Reload before saving.', 409)
    return path, updates(deepcopy(doc), body.get('updates')), revision

@bp.post('/admin/menu-api/<restaurant>/validate')
def validate(restaurant):
    _, document, revision = edited(restaurant)
    return response(document, revision)

@bp.route('/admin/menu-api/<restaurant>/draft', methods=['PUT', 'PATCH'])
def save(restaurant):
    with lock:
        path, document, _ = edited(restaurant)
        if 'menu' in document:
            document['validation'] = {**document.get('validation', {}), **validation(document)}
        atomic_save(document, path)
        return response(*read(path))
