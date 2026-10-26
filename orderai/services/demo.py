"""Single-process demo lifecycle. No fulfilment or notification side effects."""
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
import hashlib
import json
import os
import secrets
import time
import uuid

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session
from orderai.core.agent import execute_tool, tools
from orderai.core.restaurant import load_menu, load_restaurant_config, iter_menu_categories
from orderai.core.session_store import get_session_state, sessions
from orderai.core.tools import get_order

bp = Blueprint('demo', __name__)
registry_lock = RLock()
calls = {}
attempts = {}
ALLOWED = {'search_menu', 'resolve_menu_item', 'add_item_to_order', 'remove_from_order',
           'get_order', 'continue_pending_action', 'validate_order', 'confirm_order',
           'submit_order', 'start_new_order'}


class DemoError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


@dataclass
class Call:
    owner: str
    restaurant: str = 'sanis'
    identity: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    closed_at: float | None = None
    close_reason: str | None = None
    revision: int = 0
    token_requested: bool = False
    cache: dict = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)


@bp.app_errorhandler(DemoError)
def error(exc):
    return jsonify(error=exc.message, code=exc.code), exc.status


@bp.before_request
def demo_only():
    if not current_app.config['DEMO_ENABLED']:
        raise DemoError('disabled', 'Demo mode is disabled.', 404)


@bp.before_app_request
def protect_demo_editor():
    if current_app.config['DEMO_ENABLED'] and (request.endpoint or '').startswith('menu_management.'):
        raise DemoError('disabled', 'Menu administration is unavailable in demo mode.', 404)


def csrf_guard():
    expected = session.get('demo_csrf')
    supplied = request.headers.get('X-Demo-CSRF', '')
    if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
        raise DemoError('csrf', 'Reload the page before starting a call.', 403)
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        raise DemoError('origin', 'Request origin is not allowed.', 403)
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        raise DemoError('origin', 'Cross-site request is not allowed.', 403)


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DemoError('invalid_request', 'A JSON object is required.')
    return data


def expire(call):
    now = time.monotonic()
    if call.closed_at is None and (
        now - call.created >= current_app.config['DEMO_CALL_SECONDS']
        or now - call.touched >= current_app.config['DEMO_IDLE_SECONDS']
    ):
        call.closed_at, call.close_reason = now, 'expired'


def prune():
    """Caller owns registry_lock; order tools never acquire it."""
    now = time.monotonic()
    for identity, call in list(calls.items()):
        with call.lock:
            expire(call)
            if call.closed_at is not None and now - call.closed_at > current_app.config['DEMO_RETAIN_SECONDS']:
                calls.pop(identity)
                sessions.pop(identity, None)
    for key, values in list(attempts.items()):
        attempts[key] = [value for value in values if now - value < 60]
        if not attempts[key]:
            attempts.pop(key)


def owned_call(identity):
    with registry_lock:
        prune()
        call = calls.get(identity) if isinstance(identity, str) else None
        if call is None or call.owner != session.get('demo_owner'):
            raise DemoError('session_expired', 'This call is unavailable or expired. Start a new call.', 410)
        return call


def require_active(call):
    expire(call)
    if call.closed_at is not None:
        raise DemoError('call_closed', 'This call has ended. Start a new call.', 409)


def snapshot(call):
    order = deepcopy(get_order(call.identity))
    # Display amounts are computed by Python, never by the browser.
    for item in order['items']:
        item['line_total'] = round(item['price'] * item['quantity'], 2)
    return {'call_id': call.identity, 'revision': call.revision, 'order': order,
            'call_status': 'ended' if call.closed_at is not None else 'active',
            'close_reason': call.close_reason,
            'pending_action': deepcopy(get_session_state(call.identity)['pending_action'])}


def realtime_config():
    restaurant = load_restaurant_config('sanis')
    # Config identifies the actual restaurant; the public presentation uses its full name.
    instructions = f'''You are Nova, the AI receptionist for Sani's Chicken ({restaurant['name']}).
Introduce yourself as Nova when greeting the customer; keep the welcome brief.
This is a demonstration. No real restaurant order is sent. Prices and menu content
are awaiting review and are not owner-approved. Speak naturally and briefly.
Use search_menu for availability and prices; never invent products, prices or ingredients.
Use add_item_to_order only for a clear product and known quantity (1 to 20).
Ask for clarification if ambiguous. For pending additions use continue_pending_action.
Never claim an action succeeded unless its tool result confirms it.
Use get_order for the basket or total, and before removing an item. Use its exact item_id.
Ingredient changes are unavailable for this demo; do not claim to apply them.
When the customer finishes: get_order, validate_order, read back the order and total,
then ask for explicit confirmation. Only after their confirmation call confirm_order
and submit_order. Describe successful submission as a simulated demo completion.
After a submitted order use start_new_order before adding again.
Never send a real order, promise collection, payment or fulfilment.'''
    return {'type': 'realtime', 'model': 'gpt-realtime-2.1-mini',
            'audio': {'output': {'voice': 'marin'}}, 'instructions': instructions,
            'tools': [deepcopy(tool) for tool in tools if tool['name'] in ALLOWED],
            'tool_choice': 'auto'}


def demo_home():
    selected = request.args.get('restaurant', 'sanis')
    if selected != 'sanis':
        raise DemoError('wrong_restaurant', 'This demonstration is for Sani\'s Chicken.', 404)
    session.setdefault('demo_owner', str(uuid.uuid4()))
    session.setdefault('demo_csrf', secrets.token_urlsafe(32))
    session['restaurant_id'] = 'sanis'
    menu = load_menu('sanis')
    groups = [{'name': name, 'items': items} for name, items in iter_menu_categories(menu)]
    response = current_app.make_response(render_template('index.html', menu=menu, groups=groups,
        csrf=session['demo_csrf'], demo_ready=bool(current_app.config['DEMO_ACCESS_CODE'])))
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.post('/demo/calls')
def start_call():
    csrf_guard()
    data = payload()
    owner = session.get('demo_owner')
    if not owner:
        raise DemoError('session_expired', 'Reload the page.', 410)
    configured = current_app.config['DEMO_ACCESS_CODE']
    if not configured:
        raise DemoError('not_configured', 'Call access is not configured.', 503)
    supplied = data.get('access_code', '')
    with registry_lock:
        prune()
        now = time.monotonic()
        # Do not guess Render's forwarded-IP chain. A service-wide budget is
        # independent of proxy addresses, cookies and attacker-supplied headers.
        keys = [('global',), ('peer', request.remote_addr or 'unknown'), ('owner', owner)]
        limits = [30, 10, 10]
        histories = [attempts.setdefault(key, []) for key in keys]
        if any(len(history) >= limit for history, limit in zip(histories, limits)):
            raise DemoError('rate_limit', 'Too many call attempts. Try again in a minute.', 429)
        for history in histories:
            history.append(now)
        if not isinstance(supplied, str) or len(supplied) > 256 or not secrets.compare_digest(
            configured.encode(), supplied.encode()
        ):
            raise DemoError('access_denied', 'The call access code is incorrect.', 403)
        for call in calls.values():
            if call.owner == owner and call.closed_at is None:
                raise DemoError('call_active', 'Another call is active in this browser. End it first.', 409)
        if sum(call.closed_at is None for call in calls.values()) >= current_app.config['DEMO_MAX_ACTIVE']:
            raise DemoError('capacity', 'The demo is busy. Please try again shortly.', 429)
        call = Call(owner)
        calls[call.identity] = call
        get_session_state(call.identity)
        return jsonify(snapshot(call)), 201


@bp.get('/demo/calls/<identity>')
def retrieve_call(identity):
    call = owned_call(identity)
    with call.lock:
        expire(call)
        response = jsonify(snapshot(call))
        response.headers['Cache-Control'] = 'no-store'
        return response


@bp.post('/demo/calls/<identity>/heartbeat')
def heartbeat(identity):
    csrf_guard()
    call = owned_call(identity)
    with call.lock:
        require_active(call)
        call.touched = time.monotonic()
        return jsonify(snapshot(call))


@bp.post('/demo/calls/<identity>/end')
def end_call(identity):
    csrf_guard()
    call = owned_call(identity)
    with call.lock:
        if call.closed_at is None:
            call.closed_at, call.close_reason = time.monotonic(), 'ended'
        return jsonify(snapshot(call))


def validate_arguments(name, arguments):
    definition = next((tool for tool in tools if tool['name'] == name and name in ALLOWED), None)
    if definition is None:
        raise DemoError('invalid_tool', 'This tool is unavailable in the demo.')
    schema = definition['parameters']
    if not isinstance(arguments, dict) or set(arguments) - set(schema['properties']):
        raise DemoError('invalid_arguments', 'Unexpected tool arguments.')
    if set(schema.get('required', [])) - set(arguments):
        raise DemoError('invalid_arguments', 'Required tool arguments are missing.')
    for key, value in arguments.items():
        kind = schema['properties'][key]['type']
        if kind == 'integer' and (type(value) is not int or not 1 <= value <= 20):
            raise DemoError('invalid_quantity', 'Quantity must be an integer between 1 and 20.')
        if kind == 'string' and (not isinstance(value, str) or not value.strip() or len(value) > 200):
            raise DemoError('invalid_arguments', 'Tool text must contain 1 to 200 characters.')


def demo_tool():
    csrf_guard()
    data = payload()
    call = owned_call(data.get('call_id'))
    name, arguments = data.get('name'), data.get('arguments')
    validate_arguments(name, arguments)
    tool_id = data.get('tool_call_id')
    if not isinstance(tool_id, str) or not 1 <= len(tool_id) <= 200:
        raise DemoError('invalid_call_id', 'A tool call ID is required.')
    signature = json.dumps([name, arguments], sort_keys=True, allow_nan=False)
    with call.lock:
        require_active(call)
        cached = call.cache.get(tool_id)
        if cached:
            if cached[0] != signature:
                raise DemoError('duplicate_conflict', 'Tool call ID was already used for another action.', 409)
            return jsonify(deepcopy(cached[1])), cached[2]
        if len(call.cache) >= 500:
            raise DemoError('tool_limit', 'Call action limit reached. End this call.', 429)
        before = deepcopy(get_session_state(call.identity))
        status = 200
        try:
            result = execute_tool(name, arguments, restaurant_id=call.restaurant,
                                  session_id=call.identity, simulated=True)
        except Exception as exc:
            # Exception messages can contain customer input; log only the type.
            current_app.logger.error('Demo tool failed (%s)', type(exc).__name__)
            result = {'status': 'error', 'message': 'The action failed. Check the cart before continuing.'}
            status = 500
        if before != get_session_state(call.identity):
            call.revision += 1
        call.touched = time.monotonic()
        body = {**snapshot(call), 'result': deepcopy(result)}
        call.cache[tool_id] = (signature, deepcopy(body), status)
        return jsonify(body), status


def demo_credential():
    if request.method != 'POST':
        raise DemoError('method', 'Start a protected call first.', 405)
    csrf_guard()
    call = owned_call(payload().get('call_id'))
    with call.lock:
        require_active(call)
        if call.token_requested:
            raise DemoError('credential_used', 'A credential has already been requested for this call.', 409)
        call.token_requested = True
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise DemoError('api_unavailable', 'Voice service is not configured.', 503)
    try:
        response = requests.post('https://api.openai.com/v1/realtime/client_secrets',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                     'OpenAI-Safety-Identifier': hashlib.sha256(call.owner.encode()).hexdigest()},
            json={'session': realtime_config()}, timeout=(5, 20))
        current_app.logger.info(
            'Realtime credential request: status=%s request_id=%s',
            response.status_code,
            response.headers.get('x-request-id', 'unknown'),
        )
        if not response.ok:
            error_type = 'unknown'
            error_code = 'unknown'
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    upstream_error = error_body.get('error', {})
                    if isinstance(upstream_error, dict):
                        error_type = str(upstream_error.get('type', 'unknown'))[:100]
                        error_code = str(upstream_error.get('code', 'unknown'))[:100]
            except ValueError:
                pass
            current_app.logger.error(
                'Realtime API rejected request: status=%s type=%s code=%s',
                response.status_code, error_type, error_code,
            )
            raise ValueError('Realtime API rejected request')
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('value'), str):
            current_app.logger.error('Realtime API returned unexpected credential structure')
            raise ValueError('Invalid upstream credential response')
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.error(
            'Realtime session setup failed: exception=%s', type(exc).__name__
        )
        raise DemoError(
            'voice_unavailable',
            'Voice connection could not be started. Please try a new call.',
            502,
        )
    with call.lock:
        require_active(call)
        call.touched = time.monotonic()
    # Return only the browser credential, never upstream errors or permanent keys.
    result = jsonify(value=data['value'])
    result.headers['Cache-Control'] = 'no-store'
    return result
