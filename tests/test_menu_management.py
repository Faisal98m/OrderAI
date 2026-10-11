import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from orderai.paths import REPOSITORY_ROOT
from orderai.services.menu_management import bp


class MenuManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = {}
        for restaurant in ('sanis', 'burger_and_sauce'):
            folder = self.root / restaurant
            folder.mkdir()
            data = (REPOSITORY_ROOT / 'restaurants' / restaurant / 'menu.json').read_bytes()
            (folder / 'menu.json').write_bytes(data)
            self.original[restaurant] = data
        self.app = Flask(__name__, root_path=str(REPOSITORY_ROOT))
        self.app.config.update(TESTING=True, SECRET_KEY='test-only', MENU_EDITOR_ENABLED=True, MENU_RESTAURANTS_DIR=self.root)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.assertEqual(self.client.get('/admin/menu').status_code, 200)
        with self.client.session_transaction() as sess:
            self.headers = {'X-Menu-CSRF': sess['menu_csrf']}

    def tearDown(self):
        for restaurant, data in self.original.items():
            self.assertEqual((self.root / restaurant / 'menu.json').read_bytes(), data)
        self.temp.cleanup()

    def url(self, action='draft', restaurant='sanis'):
        return f'/admin/menu-api/{restaurant}/{action}'

    def initialise(self, restaurant='sanis'):
        res = self.client.post(self.url('initialise', restaurant), headers=self.headers)
        self.assertEqual(res.status_code, 200, res.json)
        return res.json

    def update(self, data, value='9.50', **fields):
        category = data['categories'][0]
        item = category['items'][0]
        return {'revision': data['revision'], 'updates': [{'category': category['key'], 'item_id': item['id'], 'price': value, **fields}]}

    def test_restaurants_independent_and_schema_preserved(self):
        a, b = self.initialise(), self.initialise('burger_and_sauce')
        self.assertNotEqual(a['restaurant'], b['restaurant'])
        self.assertEqual(self.client.put(self.url(), json=self.update(a), headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(self.url(restaurant='burger_and_sauce')).json, b)
        for restaurant, schema in [('sanis', list), ('burger_and_sauce', dict)]:
            doc = json.loads((self.root / restaurant / 'menu.draft.json').read_text('utf-8'))
            self.assertIsInstance(doc['categories'], schema)

    def test_save_reload_and_preserve_id_and_metadata(self):
        data = self.initialise()
        result = self.client.put(self.url(), json=self.update(data, name='Edited product', description='Reviewed description'), headers=self.headers)
        self.assertEqual(result.status_code, 200)
        reloaded = self.client.get(self.url()).json
        item = reloaded['categories'][0]['items'][0]
        self.assertEqual(item['price'], 9.5)
        self.assertEqual(item['id'], data['categories'][0]['items'][0]['id'])
        self.assertEqual(item['name'], 'Edited product')
        self.assertEqual(item['source_text'], data['categories'][0]['items'][0]['source_text'])

    def test_invalid_prices(self):
        data = self.initialise()
        before = (self.root / 'sanis/menu.draft.json').read_bytes()
        for value in [-1, '-0.01', 'NaN', 'Infinity', '-Infinity', 'bad', '', '2.001', True, [], {}, '1e999']:
            with self.subTest(value=value):
                res = self.client.put(self.url(), json=self.update(data, value), headers=self.headers)
                self.assertEqual(res.status_code, 400, res.json)
                self.assertEqual((self.root / 'sanis/menu.draft.json').read_bytes(), before)

    def test_zero_and_missing_prices(self):
        data = self.initialise()
        for value in ['0.00', None]:
            res = self.client.patch(self.url(), json=self.update(data, value), headers=self.headers)
            self.assertEqual(res.status_code, 200)
            data = res.json
        self.assertGreater(data['validation']['missing_prices'], 0)

    def test_unknown_restaurants(self):
        for restaurant in ['other', '..', 'sanis.json', 'SANIS']:
            self.assertEqual(self.client.get(self.url(restaurant=restaurant)).status_code, 404)
            self.assertEqual(self.client.post(self.url('initialise', restaurant), headers=self.headers).status_code, 404)

    def test_missing_malformed_and_existing_drafts(self):
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.initialise()
        self.assertEqual(self.client.post(self.url('initialise'), headers=self.headers).status_code, 409)
        for content in ['{bad', '[]', '{"categories":[null]}', '{"categories": {"bad": null}}']:
            (self.root / 'sanis/menu.draft.json').write_text(content)
            self.assertEqual(self.client.get(self.url()).status_code, 422)

    def test_validation_warnings_nonblocking_and_preview_not_saved(self):
        self.initialise()
        doc = {'restaurant': 'Sanis', 'currency': 'GBP', 'categories': [{'name': 'Test', 'items': [{'id': 'x', 'name': 'Existing test product', 'price': None, 'description': ''}]}]}
        path = self.root / 'sanis/menu.draft.json'
        path.write_text(json.dumps(doc))
        data = self.client.get(self.url()).json
        self.assertEqual(data['validation']['missing_prices'], 1)
        self.assertEqual(data['validation']['missing_descriptions'], 1)
        before = path.read_bytes()
        result = self.client.post(self.url('validate'), json=self.update(data, '3.20'), headers=self.headers).json
        self.assertTrue(result['validation']['ready_to_publish'])
        self.assertEqual(result['validation']['warning_count'], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_wrong_category_ambiguous_ids_and_batch_atomicity(self):
        data = self.initialise()
        body = self.update(data)
        body['updates'][0]['category'] = 'wrong'
        self.assertEqual(self.client.put(self.url(), json=body, headers=self.headers).status_code, 409)
        body = self.update(data)
        body['updates'].append({**body['updates'][0], 'item_id': 'not-present'})
        before = (self.root / 'sanis/menu.draft.json').read_bytes()
        self.assertEqual(self.client.put(self.url(), json=body, headers=self.headers).status_code, 409)
        self.assertEqual((self.root / 'sanis/menu.draft.json').read_bytes(), before)
        doc = json.loads(before)
        doc['categories'][0]['items'].append(deepcopy(doc['categories'][0]['items'][0]))
        (self.root / 'sanis/menu.draft.json').write_text(json.dumps(doc))
        data = self.client.get(self.url()).json
        self.assertFalse(data['validation']['ready_to_publish'])
        self.assertEqual(self.client.put(self.url(), json=self.update(data), headers=self.headers).status_code, 409)

    def test_stale_revision(self):
        data = self.initialise()
        self.assertEqual(self.client.put(self.url(), json=self.update(data), headers=self.headers).status_code, 200)
        self.assertEqual(self.client.put(self.url(), json=self.update(data, '10'), headers=self.headers).status_code, 409)

    def test_disabled_remote_csrf_origin_and_host(self):
        self.app.config['MENU_EDITOR_ENABLED'] = False
        self.assertEqual(self.client.get('/admin/menu').status_code, 404)
        self.app.config['MENU_EDITOR_ENABLED'] = True
        self.assertEqual(self.client.get('/admin/menu', environ_overrides={'REMOTE_ADDR': '10.0.0.1'}).status_code, 403)
        self.assertEqual(self.client.get('/admin/menu', headers={'Host': 'evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/admin/menu', headers={'X-Forwarded-For': '127.0.0.1'}).status_code, 403)
        self.assertEqual(self.client.post(self.url('initialise')).status_code, 403)
        self.assertEqual(self.client.post(self.url('initialise'), headers={**self.headers, 'Origin': 'https://evil.example'}).status_code, 403)

    def test_visual_wrapper_preserves_review_and_source(self):
        doc = {'draft_version': 'visual_pdf_v1', 'menu': json.loads(self.original['sanis']), 'validation': {'blocking_issues': [{'type': 'human_review_required'}]}}
        source = self.root / 'import.json'
        source.write_text(json.dumps(doc))
        self.app.config['MENU_IMPORT_SOURCES'] = {'sanis': source}
        data = self.initialise()
        res = self.client.put(self.url(), json=self.update(data), headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn('human_review_required', [i['type'] for i in res.json['validation']['blocking_issues']])
        stored = json.loads((self.root / 'sanis/menu.draft.json').read_text())
        self.assertEqual(stored['draft_version'], 'visual_pdf_v1')
        self.assertEqual(json.loads(source.read_text()), doc)

    def test_atomic_failure_preserves_file(self):
        data = self.initialise()
        path = self.root / 'sanis/menu.draft.json'
        before = path.read_bytes()
        with patch('orderai.services.menu_management.os.replace', side_effect=OSError('test failure')):
            self.assertEqual(self.client.put(self.url(), json=self.update(data), headers=self.headers).status_code, 500)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('.menu-draft-*')), [])

    def test_invalid_requests_and_name(self):
        data = self.initialise()
        for body in [None, [], {}, {'revision': data['revision'], 'updates': 'bad'}, self.update(data, name='')]:
            self.assertIn(self.client.put(self.url(), json=body, headers=self.headers).status_code, [400, 409])

    def complete_extraction(self):
        return REPOSITORY_ROOT / 'tests/output/multipage_pdf/complete_draft_20261010T142547Z_bd01b3de8e99422b92cceaf08321ac56.json'

    def test_complete_extraction_all_products_and_presentation_labels(self):
        source = self.complete_extraction()
        original = source.read_bytes()
        document = json.loads(original)
        self.app.config['MENU_IMPORT_SOURCES'] = {'burger_and_sauce': source}
        data = self.initialise('burger_and_sauce')
        self.assertEqual([len(c['items']) for c in data['categories']], [3, 12, 13, 3, 4, 8])
        self.assertEqual(data['categories'][1]['display_name'], 'Uncategorised — Page 2')
        self.assertEqual(data['categories'][2]['display_name'], 'Uncategorised — Page 3')
        self.assertIsNone(data['categories'][1]['name'])
        self.assertIsNone(data['categories'][2]['name'])
        self.assertEqual(data['validation']['item_count'], 43)
        self.assertEqual(data['validation']['missing_prices'], 43)
        self.assertEqual(data['validation']['missing_descriptions'], 19)
        self.assertIsNone(data['currency'])
        self.assertFalse(data['validation']['ready_to_publish'])
        self.assertEqual(data['validation']['extraction_review_issue_count'], 224)
        kinds = [i['type'] for i in data['validation']['blocking_issues']]
        self.assertEqual(kinds.count('cross_page_reference_review'), 4)
        self.assertIn('multipage_experimental_draft', kinds)
        self.assertEqual(kinds.count('unsupported_currency'), 1)
        self.assertTrue(all(i['price'] is None for c in data['categories'] for i in c['items']))
        stored = json.loads((self.root / 'burger_and_sauce/menu.draft.json').read_text('utf-8'))
        self.assertEqual(stored, document)
        self.assertEqual(source.read_bytes(), original)

    def test_nullable_extraction_edit_reload_preserves_all_evidence(self):
        source = self.complete_extraction()
        original = source.read_bytes()
        document = json.loads(original)
        self.app.config['MENU_IMPORT_SOURCES'] = {'burger_and_sauce': source}
        data = self.initialise('burger_and_sauce')
        sanis = self.initialise()
        item = data['categories'][1]['items'][0]
        new_name = item['name'] + ' (draft test)'
        body = {'revision': data['revision'], 'updates': [{'category': '1', 'item_id': item['id'], 'name': new_name}]}
        result = self.client.patch(self.url(restaurant='burger_and_sauce'), json=body, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        reloaded = self.client.get(self.url(restaurant='burger_and_sauce')).json
        self.assertEqual(reloaded['categories'][1]['items'][0]['name'], new_name)
        stored = json.loads((self.root / 'burger_and_sauce/menu.draft.json').read_text('utf-8'))
        expected_menu = deepcopy(document['menu'])
        expected_menu['categories'][1]['items'][0]['name'] = new_name
        self.assertEqual(stored['menu'], expected_menu)
        for key in document.keys() - {'menu', 'validation'}:
            self.assertEqual(stored[key], document[key])
        for key in document['validation'].keys() - {'blocking_issues', 'ready_to_publish', 'blocking_issue_count', 'warnings'}:
            self.assertEqual(stored['validation'][key], document['validation'][key])
        original_review = [i for i in document['validation']['blocking_issues'] if i['type'] != 'missing_price']
        for issue in original_review:
            self.assertIn(issue, stored['validation']['blocking_issues'])
        self.assertEqual(self.client.get(self.url()).json, sanis)
        self.assertEqual(source.read_bytes(), original)
        body['revision'] = reloaded['revision']
        self.assertEqual(self.client.patch(self.url(restaurant='burger_and_sauce'), json=body, headers=self.headers).status_code, 200)
        final = self.client.get(self.url(restaurant='burger_and_sauce')).json
        self.assertEqual(sum(i['type'] == 'unsupported_currency' for i in final['validation']['blocking_issues']), 1)

    def test_invalid_category_name_still_rejected(self):
        self.initialise()
        path = self.root / 'sanis/menu.draft.json'
        doc = json.loads(path.read_text('utf-8'))
        doc['categories'][0]['name'] = 123
        path.write_text(json.dumps(doc))
        self.assertEqual(self.client.get(self.url()).status_code, 422)


if __name__ == '__main__':
    unittest.main()
