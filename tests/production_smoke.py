"""Actual production entry-point/route smoke test with external requests blocked.

Uses real SDK construction with unusable fixture credentials, never live APIs.
On Windows this verifies WSGI behaviour, not Gunicorn's Unix process model.
"""
from pathlib import Path
import os
import sys
from unittest.mock import Mock, patch


def main():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    os.environ.update(ORDERAI_DEMO_ENABLED='1', ORDERAI_MENU_EDITOR_ENABLED='0',
        ORDERAI_DEMO_ACCESS_CODE='offline-access-code-fixture',
        FLASK_SECRET_KEY='offline-session-fixture-' + 'x' * 32,
        OPENAI_API_KEY='offline-api-fixture', RENDER='true',
        RENDER_EXTERNAL_HOSTNAME='demo.example', PYTHON_DOTENV_DISABLED='1')
    with patch('requests.sessions.Session.request', side_effect=AssertionError('No external HTTP')), \
         patch('httpx2.Client.send', side_effect=AssertionError('No external SDK HTTP')), \
         patch('httpx2.AsyncClient.send', side_effect=AssertionError('No external SDK HTTP')), \
         patch('twilio.rest.Client', side_effect=AssertionError('No Twilio')):
        from orderai.services.production import app
        from orderai.services import demo
        from orderai.core import tools
        with patch.object(tools, 'save_order', side_effect=AssertionError('No database writes')), \
             patch.object(tools, 'send_whatsapp_message', side_effect=AssertionError('No WhatsApp')):
            peer = {'REMOTE_ADDR': '10.0.0.5'}
            proxy = {'X-Forwarded-Proto': 'https', 'Origin': 'https://demo.example'}
            client = app.test_client()
            def request(method, path, body=None, headers=None):
                return client.open(path, method=method, json=body,
                    base_url='http://demo.example', headers={**proxy, **(headers or {})}, environ_overrides=peer)
            home = request('GET', '/')
            assert home.status_code == 200
            for secret in ('OPENAI_API_KEY', 'FLASK_SECRET_KEY', 'ORDERAI_DEMO_ACCESS_CODE'):
                assert os.environ[secret] not in home.get_data(as_text=True)
            with client.session_transaction(base_url='https://demo.example') as cookie:
                csrf = {'X-Demo-CSRF': cookie['demo_csrf']}
            assert request('GET', '/healthz').json == {'status': 'ok'}
            assert request('GET', '/?restaurant=burger_and_sauce').status_code == 404
            for path in ('/admin/orders', '/admin/orders-data', '/admin/menu', '/admin/menu-api/sanis/draft'):
                assert request('GET', path).status_code == 404
            for path in ('/chat', '/transcribe'):
                assert request('POST', path, {}).status_code == 403
            assert request('POST', '/admin/orders/1/status', {}).status_code == 404
            assert request('POST', '/admin/menu-api/sanis/initialise', {}).status_code == 404
            assert request('GET', '/realtime-session').status_code == 405
            assert request('POST', '/realtime-session', {}).status_code == 403
            assert request('POST', '/demo/calls', {'access_code': os.environ['ORDERAI_DEMO_ACCESS_CODE']}).status_code == 403
            assert request('POST', '/demo/calls', {'access_code': 'wrong'}, csrf).status_code == 403
            assert request('POST', '/demo/calls', {'access_code': os.environ['ORDERAI_DEMO_ACCESS_CODE']},
                {**csrf, 'Origin': 'https://attacker.example'}).status_code == 403
            started = request('POST', '/demo/calls', {'access_code': os.environ['ORDERAI_DEMO_ACCESS_CODE']}, csrf)
            assert started.status_code == 201, started.json
            identity = started.json['call_id']
            assert request('POST', '/realtime-session', {'call_id': identity},
                {**csrf, 'Origin': 'https://attacker.example'}).status_code == 403
            # Check credential response with a mock, never connect to OpenAI.
            upstream = Mock(ok=True)
            upstream.json.return_value = {'value': 'offline-ephemeral-fixture'}
            with patch.object(demo.requests, 'post', return_value=upstream) as mint:
                token = request('POST', '/realtime-session', {'call_id': identity}, csrf)
                assert token.json == {'value': 'offline-ephemeral-fixture'}
                assert mint.call_count == 1
            def tool(name, arguments, tool_id):
                return request('POST', '/realtime-tool', {'call_id': identity, 'tool_call_id': tool_id,
                    'name': name, 'arguments': arguments}, csrf)
            added = tool('add_item_to_order', {'search_term': 'Firecracker', 'quantity': 2}, 'add')
            assert added.json['order']['total'] == 7.98
            assert tool('add_item_to_order', {'search_term': 'Firecracker', 'quantity': 2}, 'add').json == added.json
            for name in ('validate_order', 'confirm_order', 'submit_order'):
                result = tool(name, {}, name)
                assert result.status_code == 200
            assert result.json['result']['simulated'] is True
            other = app.test_client()
            assert other.get(f'/demo/calls/{identity}', base_url='https://demo.example').status_code == 410
            assert request('POST', f'/demo/calls/{identity}/end', {}, csrf).status_code == 200
            assert tool('get_order', {}, 'late').status_code == 409
            for path in ('/.env', '/orderai.db', '/static/../.env', '/static/../orderai.db'):
                assert request('GET', path).status_code == 404
            assert os.environ['OPENAI_API_KEY'] not in request('GET', '/static/demo.js').get_data(as_text=True)
            # Vary socket peers and all forwarding headers. The global cap still applies.
            demo.attempts.clear()
            for index in range(31):
                visitor = app.test_client()
                visitor.get('/', base_url='https://demo.example')
                with visitor.session_transaction(base_url='https://demo.example') as cookie:
                    token = cookie['demo_csrf']
                result = visitor.post('/demo/calls', base_url='http://demo.example',
                    headers={**proxy, 'X-Demo-CSRF': token, 'X-Forwarded-For': str(index),
                             'X-Forwarded-Host': 'attacker.example'},
                    environ_overrides={'REMOTE_ADDR': f'10.0.1.{index}'}, json={'access_code': 'wrong'})
                assert result.status_code == (403 if index < 30 else 429)
    print('Production WSGI smoke: passed (startup, HTTPS proxy, cookies, routes, isolation, simulation, limits)')


if __name__ == '__main__':
    main()
