"""Offline demo regressions: SDK construction, HTTP and fulfilment are mocked."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patches = [patch('openai.OpenAI', return_value=Mock()),
                       patch('dotenv.load_dotenv', return_value=False),
                       patch('twilio.rest.Client', side_effect=AssertionError('No Twilio calls')),
                       patch('requests.sessions.Session.request', side_effect=AssertionError('No network'))]
        for item in cls.patches:
            item.start()
        cls.web = importlib.import_module('orderai.services.web')
        cls.demo = importlib.import_module('orderai.services.demo')
        cls.core = importlib.import_module('orderai.core.tools')

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.patches):
            item.stop()

    def setUp(self):
        self.config = dict(self.web.app.config)
        self.web.app.config.update(TESTING=True, DEMO_ENABLED=True, MENU_EDITOR_ENABLED=False, DEMO_ACCESS_CODE='local-test-code',
                                   DEMO_IDLE_SECONDS=45, DEMO_CALL_SECONDS=600)
        self.demo.calls.clear()
        self.demo.attempts.clear()
        self.clients = []
        self.client, self.headers = self.browser()
        self.no_save = patch.object(self.core, 'save_order', side_effect=AssertionError('No persistence for demos'))
        self.no_whatsapp = patch.object(self.core, 'send_whatsapp_message', side_effect=AssertionError('No WhatsApp'))
        self.no_save.start()
        self.no_whatsapp.start()

    def tearDown(self):
        for call in self.demo.calls.values():
            self.demo.sessions.pop(call.identity, None)
        self.demo.calls.clear()
        self.demo.attempts.clear()
        self.web.app.config.clear()
        self.web.app.config.update(self.config)
        self.no_save.stop()
        self.no_whatsapp.stop()

    def browser(self):
        client = self.web.app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as cookie:
            headers = {'X-Demo-CSRF': cookie['demo_csrf']}
        self.clients.append(client)
        return client, headers

    def start(self, client=None, headers=None):
        response = (client or self.client).post('/demo/calls', headers=headers or self.headers,
                                               json={'access_code': 'local-test-code'})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['call_id']

    def tool(self, identity, name, arguments=None, tool_id=None, client=None, headers=None):
        return (client or self.client).post('/realtime-tool', headers=headers or self.headers,
            json={'call_id': identity, 'tool_call_id': tool_id or str(uuid.uuid4()),
                  'name': name, 'arguments': arguments or {}})

    def test_public_menu_and_review_metadata(self):
        response = self.client.get('/')
        html = response.get_data(as_text=True)
        self.assertIn('Demonstration only — no real order will be sent.', html)
        self.assertIn('Burger Loaded Box', html)
        self.assertIn('£7.99', html)
        self.assertEqual(html.count('class="product"'), 22)
        self.assertEqual(html.count('Needs review'), 22)
        self.assertIn('not been approved', html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_sanis_search_add_remove_total(self):
        identity = self.start()
        result = self.tool(identity, 'search_menu', {'search_term': 'Firecracker'}).json
        self.assertEqual(result['result'][0]['id'], 'firecracker')
        added = self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 2}).json
        self.assertEqual(added['order']['total'], 7.98)
        self.assertEqual(added['order']['items'][0]['line_total'], 7.98)
        self.assertEqual(added['revision'], 1)
        removed = self.tool(identity, 'remove_from_order', {'item_id': 'firecracker', 'quantity': 1}).json
        self.assertEqual(removed['order']['total'], 3.99)
        self.assertEqual(removed['order']['items'][0]['quantity'], 1)
        self.assertEqual(removed['revision'], 2)

    def test_burger_and_sauce_regression(self):
        identity = 'demo-test-burger'
        try:
            self.assertEqual(self.core.search_menu('Chicken Burger', 'burger_and_sauce')[0]['id'], 'BS001')
            self.core.add_item_to_order('Chicken Burger', 2, 'burger_and_sauce', identity)
            self.assertEqual(self.core.get_order(identity)['total'], 15.98)
            self.core.remove_from_order('BS001', 1, identity)
            self.assertEqual(self.core.get_order(identity)['total'], 7.99)
        finally:
            self.demo.sessions.pop(identity, None)

    def test_published_menus_not_rewritten(self):
        from orderai.core.restaurant import RESTAURANTS_DIR
        original = {name: (RESTAURANTS_DIR / name / 'menu.json').read_bytes()
                    for name in ('sanis', 'burger_and_sauce')}
        self.test_sanis_search_add_remove_total()
        for name, value in original.items():
            self.assertEqual((RESTAURANTS_DIR / name / 'menu.json').read_bytes(), value)

    def test_restaurant_pinned_and_bypass_routes_blocked(self):
        self.assertEqual(self.client.get('/?restaurant=burger_and_sauce').status_code, 404)
        for route in ('/chat', '/transcribe'):
            self.assertEqual(self.client.post(route, json={}).status_code, 403)
        for route in ('/admin/orders', '/admin/orders-data', '/admin/menu'):
            self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual(self.client.post('/admin/orders/1/status', json={'status': 'ready'}).status_code, 404)
        identity = self.start()
        response = self.client.post('/realtime-tool', headers=self.headers, json={
            'call_id': identity, 'tool_call_id': 'tamper', 'name': 'search_menu',
            'arguments': {'search_term': 'Chicken Burger', 'restaurant_id': 'burger_and_sauce'}})
        self.assertEqual(response.status_code, 400)

    def test_non_demo_restaurant_selection_and_original_interface(self):
        self.web.app.config['DEMO_ENABLED'] = False
        response = self.client.get('/?restaurant=burger_and_sauce')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Start Realtime Test', response.get_data(as_text=True))
        self.assertEqual(self.client.post('/demo/calls', json={}).status_code, 404)

    def test_menu_editor_stays_blocked_even_when_enabled(self):
        self.web.app.config['MENU_EDITOR_ENABLED'] = True
        self.assertEqual(self.client.get('/admin/menu').status_code, 404)
        self.assertEqual(self.client.post('/admin/menu-api/sanis/initialise', json={}).status_code, 404)

    def test_session_isolation(self):
        first = self.start()
        second_client, headers = self.browser()
        second = self.start(second_client, headers)
        self.tool(first, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 2})
        self.assertEqual(self.tool(second, 'get_order', client=second_client, headers=headers).json['order']['total'], 0)
        self.assertEqual(second_client.get(f'/demo/calls/{first}').status_code, 410)
        self.assertEqual(self.tool(first, 'get_order', client=second_client, headers=headers).status_code, 410)
        self.assertEqual(second_client.post(f'/demo/calls/{first}/end', headers=headers).status_code, 410)

    def test_access_csrf_origin_missing_configuration(self):
        self.assertEqual(self.client.post('/demo/calls', json={'access_code': 'local-test-code'}).status_code, 403)
        self.assertEqual(self.client.post('/demo/calls', headers=self.headers, json={'access_code': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.post('/demo/calls', headers={**self.headers, 'Origin': 'https://other.test'},
            json={'access_code': 'local-test-code'}).status_code, 403)
        self.web.app.config['DEMO_ACCESS_CODE'] = ''
        self.assertEqual(self.client.post('/demo/calls', headers=self.headers, json={'access_code': ''}).status_code, 503)

    def test_rate_limit_and_capacity(self):
        for _ in range(10):
            self.client.post('/demo/calls', headers=self.headers, json={'access_code': 'wrong'})
        self.assertEqual(self.client.post('/demo/calls', headers=self.headers, json={'access_code': 'wrong'}).status_code, 429)
        self.demo.attempts.clear()
        self.web.app.config['DEMO_MAX_ACTIVE'] = 1
        self.start()
        other, headers = self.browser()
        self.assertEqual(other.post('/demo/calls', headers=headers, json={'access_code': 'local-test-code'}).status_code, 429)

    def test_fresh_order_one_active_end_idempotent(self):
        first = self.start()
        self.tool(first, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1})
        self.assertEqual(self.client.post('/demo/calls', headers=self.headers, json={'access_code': 'local-test-code'}).status_code, 409)
        for _ in range(2):
            self.assertEqual(self.client.post(f'/demo/calls/{first}/end', headers=self.headers).status_code, 200)
        second = self.start()
        self.assertNotEqual(first, second)
        self.assertEqual(self.tool(second, 'get_order').json['order']['total'], 0)

    def test_simulation_and_lifecycle_guards(self):
        identity = self.start()
        self.assertEqual(self.tool(identity, 'confirm_order').json['result']['status'], 'confirmation_not_allowed')
        self.assertEqual(self.tool(identity, 'submit_order').json['result']['status'], 'submission_not_allowed')
        self.assertEqual(self.tool(identity, 'validate_order').json['result']['reason'], 'empty_order')
        self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1})
        self.tool(identity, 'validate_order')
        self.tool(identity, 'confirm_order')
        self.assertEqual(self.tool(identity, 'remove_from_order', {'item_id': 'firecracker', 'quantity': 1}).json['result']['status'], 'order_locked')
        self.assertEqual(self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1}).json['result']['status'], 'order_locked')
        result = self.tool(identity, 'submit_order').json
        self.assertTrue(result['result']['simulated'])
        self.assertEqual(result['order']['status'], 'submitted')
        self.assertEqual(self.tool(identity, 'validate_order').json['result']['status'], 'validation_not_allowed')
        self.assertEqual(self.tool(identity, 'submit_order').json['result']['status'], 'submission_not_allowed')
        self.assertEqual(self.tool(identity, 'start_new_order').json['order']['items'], [])

    def test_non_demo_submission_still_saves_and_notifies(self):
        identity = 'demo-test-production'
        try:
            self.core.add_item_to_order('Firecracker', 1, 'sanis', identity)
            self.core.validate_order(session_id=identity)
            self.core.confirm_order(session_id=identity)
            with patch.object(self.core, 'save_order', return_value=12) as save, \
                 patch.object(self.core, 'send_whatsapp_message', return_value={'status': 'sent'}) as send:
                result = self.core.submit_order(identity)
                self.assertEqual(result['order_id'], 12)
                save.assert_called_once()
                send.assert_called_once()
        finally:
            self.demo.sessions.pop(identity, None)

    def test_duplicate_action_and_conflicting_id(self):
        identity = self.start()
        arguments = {'search_term': 'Firecracker', 'quantity': 2}
        first = self.tool(identity, 'add_item_to_order', arguments, 'same').json
        second = self.tool(identity, 'add_item_to_order', arguments, 'same').json
        self.assertEqual(first, second)
        self.assertEqual(self.tool(identity, 'get_order').json['order']['total'], 7.98)
        self.assertEqual(self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1}, 'same').status_code, 409)

    def test_failed_mutation_cached_at_most_once(self):
        identity = self.start()
        original = self.demo.execute_tool
        def partially_failing(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Injected post-mutation failure')
        with patch.object(self.demo, 'execute_tool', side_effect=partially_failing) as execute, \
             patch.object(self.web.app.logger, 'exception'):
            for _ in range(2):
                response = self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1}, 'failure')
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json['order']['total'], 3.99)
            self.assertEqual(execute.call_count, 1)

    def test_closed_lost_and_expired_calls(self):
        identity = self.start()
        self.client.post(f'/demo/calls/{identity}/end', headers=self.headers)
        self.assertEqual(self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1}).status_code, 409)
        self.assertEqual(self.tool('lost', 'get_order').status_code, 410)
        self.assertEqual(self.client.get(f'/demo/calls/{identity}').json['call_status'], 'ended')
        fresh = self.start()
        self.demo.calls[fresh].touched -= 46
        self.assertEqual(self.tool(fresh, 'get_order').status_code, 409)
        self.assertEqual(self.client.get(f'/demo/calls/{fresh}').json['close_reason'], 'expired')

    def test_heartbeat_disconnect_and_retention(self):
        identity = self.start()
        self.demo.calls[identity].touched -= 20
        self.assertEqual(self.client.post(f'/demo/calls/{identity}/heartbeat', headers=self.headers).status_code, 200)
        self.assertLess(time.monotonic() - self.demo.calls[identity].touched, 1)
        self.demo.calls[identity].created -= 601
        self.assertEqual(self.client.post(f'/demo/calls/{identity}/heartbeat', headers=self.headers).status_code, 409)
        self.demo.calls[identity].closed_at -= 1801
        self.assertEqual(self.client.get(f'/demo/calls/{identity}').status_code, 410)
        self.assertNotIn(identity, self.demo.sessions)

    def test_refresh_reads_cart_without_minting_credentials(self):
        identity = self.start()
        self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1})
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get(f'/demo/calls/{identity}').json['order']['total'], 3.99)
        self.assertFalse(self.demo.calls[identity].token_requested)

    def test_argument_validation_and_quantity_cap(self):
        identity = self.start()
        for value in (0, -1, True, 1.5, '2', 21):
            self.assertEqual(self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': value}).status_code, 400)
        for name, args in (('unknown', {}), ('customize_order_item', {}), ('add_item_to_order', {'search_term': 'x'}),
                           ('search_menu', {'search_term': ''}), ('get_order', {'session_id': 'other'})):
            self.assertEqual(self.tool(identity, name, args).status_code, 400)
        self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 20})
        self.assertEqual(self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1}).json['result']['status'], 'invalid_quantity')
        self.assertEqual(self.tool(identity, 'get_order').json['order']['total'], 79.8)

    def test_ambiguity_not_added_and_clarification(self):
        identity = self.start()
        result = self.tool(identity, 'add_item_to_order', {'search_term': 'loaded box', 'quantity': 2}).json
        self.assertEqual(result['result']['status'], 'ambiguous')
        self.assertEqual(result['order']['total'], 0)
        result = self.tool(identity, 'continue_pending_action', {'clarification': 'burger'}).json
        self.assertEqual(result['order']['total'], 15.98)
        self.assertIsNone(result['pending_action']['action'])

    def test_concurrent_mutations_and_duplicate_execution(self):
        identity = self.start()
        cookie = self.client.get_cookie('session').value
        original = self.demo.execute_tool
        running = 0
        highest = 0
        guard = threading.Lock()
        def execute(*args, **kwargs):
            nonlocal running, highest
            with guard:
                running += 1
                highest = max(highest, running)
            try:
                time.sleep(.01)
                return original(*args, **kwargs)
            finally:
                with guard:
                    running -= 1
        def action(index):
            client = self.web.app.test_client()
            client.set_cookie('session', cookie)
            return self.tool(identity, 'add_item_to_order', {'search_term': 'Firecracker', 'quantity': 1},
                             f'concurrent-{index // 2}', client=client).status_code
        with patch.object(self.demo, 'execute_tool', side_effect=execute) as mocked, ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(action, range(16))), [200] * 16)
            self.assertEqual(mocked.call_count, 8)
        self.assertEqual(highest, 1)
        result = self.tool(identity, 'get_order').json
        self.assertEqual(result['order']['items'][0]['quantity'], 8)
        self.assertEqual(result['order']['total'], 31.92)

    def test_credential_configuration_and_single_mint(self):
        identity = self.start()
        self.assertEqual(self.client.get('/realtime-session').status_code, 405)
        response = Mock(ok=True)
        response.json.return_value = {'value': 'ephemeral-only', 'unused': 'not-forwarded'}
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'offline-secret'}), \
             patch.object(self.demo.requests, 'post', return_value=response) as post:
            result = self.client.post('/realtime-session', headers=self.headers, json={'call_id': identity})
            self.assertEqual(result.json, {'value': 'ephemeral-only'})
            self.assertEqual(result.headers['Cache-Control'], 'no-store')
            config = post.call_args.kwargs['json']['session']
            self.assertIn("Sani's Chicken", config['instructions'])
            self.assertNotIn('Burger House', config['instructions'])
            removal = next(tool for tool in config['tools'] if tool['name'] == 'remove_from_order')
            self.assertIn('item_id', removal['parameters']['properties'])
            self.assertEqual(self.client.post('/realtime-session', headers=self.headers, json={'call_id': identity}).status_code, 409)
            post.assert_called_once()

    def test_credential_failure_sanitised_and_closed_call_blocked(self):
        identity = self.start()
        response = Mock(ok=False)
        response.json.return_value = {'error': {'message': 'private upstream details'}}
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'offline-secret'}), patch.object(self.demo.requests, 'post', return_value=response):
            result = self.client.post('/realtime-session', headers=self.headers, json={'call_id': identity})
            self.assertEqual(result.status_code, 502)
            self.assertNotIn('private', result.get_data(as_text=True))
        self.client.post(f'/demo/calls/{identity}/end', headers=self.headers)
        self.assertEqual(self.client.post('/realtime-session', headers=self.headers, json={'call_id': identity}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
