import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from diligence.server import App, Handler
from tests.test_pipeline import FakeLM, fixture


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(self.tmp.name, FakeLM())
        self.did = fixture(self.app.store)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.app = self.app
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        raw = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=raw, headers=headers or {})
        return self.opener.open(req, timeout=3)

    def test_status_and_static_page(self):
        with self.request('/api/status') as res:
            status = json.load(res)
        self.assertEqual(status['token'], self.app.token)
        self.assertTrue(status['lm']['connected'])
        with self.request('/') as res:
            self.assertIn(b'Understand the business', res.read())
            self.assertIn("frame-ancestors 'none'", res.headers['Content-Security-Policy'])

    def test_mutation_requires_local_token(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/runs', {'deck_id': self.did, 'model': 'qwen-fixture'})
        self.assertEqual(raised.exception.code, 403)
        self.assertEqual(json.load(raised.exception)['code'], 'stale_token')

    def test_token_rotation_rejects_before_starting_analysis(self):
        old = self.app.token
        self.app.token = 'rotated-test-token'
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/runs', {'deck_id': self.did, 'model': 'qwen-fixture'}, {'X-Diligence-Token': old})
        self.assertEqual(json.load(raised.exception)['code'], 'stale_token')
        self.assertEqual(self.app.store.runs(self.did), [])
        self.assertIsNone(self.app.active_run)

    def test_cross_origin_and_untrusted_host_rejected(self):
        for headers in [{'Host': 'evil.example'}, {'Origin': 'https://evil.example', 'X-Diligence-Token': self.app.token}]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request('/api/runs', {}, headers)
            self.assertEqual(raised.exception.code, 403)
            self.assertEqual(json.load(raised.exception)['code'], 'invalid_origin')

    def test_decks_are_listed_and_path_traversal_is_rejected(self):
        with self.request('/api/decks') as res:
            self.assertEqual(json.load(res)[0]['id'], self.did)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/../LICENSE')
        self.assertEqual(raised.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
