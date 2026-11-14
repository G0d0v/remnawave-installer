import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from godov.api import API, APIError, bootstrap_panel, provision
from godov.config import Config


class Handler(BaseHTTPRequestHandler):
    records = {}
    calls = []
    headers_seen = []

    def log_message(self, *args):
        pass

    def respond(self, status, payload):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        self.calls.append(('GET', self.path))
        self.headers_seen.append((self.path, self.headers.get('X-Remnawave-Client-Type')))
        if self.path == '/api/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://example.invalid/stolen')
            self.end_headers()
            return
        if self.path == '/api/forbidden':
            return self.respond(403, {'message': 'sensitive-body-should-not-be-logged'})
        if self.path == '/api/auth/status':
            data = {'isRegisterAllowed': True}
        elif self.path == '/api/tokens/scopes':
            if self.headers.get('X-Remnawave-Client-Type') != 'browser':
                return self.respond(403, {'message': 'admin client type required'})
            groups = ['nodes', 'hosts', 'config-profiles', 'internal-squads', 'keygen', 'system',
                      'users', 'subscriptions', 'external-squads', 'hwid-user-devices']
            data = {'resources': [{'resource': name, 'endpoints': [{'key': name + ':read'}]} for name in groups]}
        elif self.path == '/api/system/tools/x25519/generate':
            data = {'keypairs': [{'privateKey': 'private', 'publicKey': 'public'}]}
        elif self.path == '/api/keygen':
            data = {'secretKey': 'base64-secret'}
        elif self.path == '/api/config-profiles':
            data = {'configProfiles': self.records.get(self.path, [])}
        elif self.path == '/api/internal-squads':
            data = {'internalSquads': self.records.get(self.path, [])}
        else:
            data = self.records.get(self.path, [])
        self.respond(200, {'response': data})

    def do_POST(self):
        self.calls.append(('POST', self.path))
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/auth/register':
            return self.respond(201, {'response': {'accessToken': 'admin-jwt'}})
        if self.path == '/api/tokens':
            return self.respond(201, {'response': {'token': data['name'] + '-issued-token'}})
        data['uuid'] = str(len(self.calls))
        if self.path == '/api/config-profiles':
            data['inbounds'] = [{'uuid': 'inbound-1', 'tag': 'G0D0V_VLESS_REALITY'}]
        self.records.setdefault(self.path, []).append(data)
        self.respond(201, {'response': data})


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.records = {}
        Handler.calls = []
        Handler.headers_seen = []
        self.api = API(self.url, 'secret-token', allow_local=True)

    def test_refuses_plain_http_remote_panels(self):
        with self.assertRaises(ValueError):
            API('http://remote.example.com', 'secret-token')

    def test_admin_bootstrap_sets_required_client_header_only_for_token_issuance(self):
        c = Config(mode='panel')
        bootstrap_panel(self.api, c, lambda: None)
        self.api.get('/api/nodes')
        self.assertTrue(c.api_token)
        self.assertTrue(c.bot_token)
        scope_headers = [value for path, value in Handler.headers_seen if path == '/api/tokens/scopes']
        self.assertEqual(scope_headers, ['browser', 'browser'])
        self.assertEqual(Handler.headers_seen[-1], ('/api/nodes', None))

    def test_never_follows_redirects_with_a_bearer_token(self):
        with self.assertRaises(APIError) as error:
            self.api.get('/api/redirect')
        self.assertEqual(error.exception.status, 302)
        self.assertEqual(Handler.calls, [('GET', '/api/redirect')])

    def test_error_contains_no_credentials_or_response_body(self):
        with self.assertRaises(APIError) as error:
            self.api.get('/api/forbidden')
        self.assertNotIn('sensitive-body', str(error.exception))
        self.assertNotIn('secret-token', str(error.exception))
        self.assertEqual(error.exception.status, 403)

    def test_provision_resumes_without_duplicate_resources(self):
        c = Config(mode='panel-node', node_domain='node.example.com', node_address='node.example.com')
        saves = []
        provision(self.api, c, lambda: saves.append(dict(c.resources)))
        first = dict(c.resources)
        posts = len([x for x in Handler.calls if x[0] == 'POST'])
        provision(self.api, c, lambda: None)
        self.assertEqual(first, c.resources)
        self.assertEqual(posts, len([x for x in Handler.calls if x[0] == 'POST']))
        self.assertGreaterEqual(len(saves), 4)
        self.assertEqual(c.node_secret, 'base64-secret')
        self.assertEqual(Handler.records['/api/nodes'][0]['address'], 'host.docker.internal')
        self.assertEqual(Handler.records['/api/hosts'][0]['inbound']['configProfileInboundUuid'], 'inbound-1')

    def test_recovers_ambiguous_create_by_name_after_state_was_not_saved(self):
        c = Config(mode='attach', node_domain='node.example.com', node_address='node.example.com')
        provision(self.api, c, lambda: None)
        c.resources = {}
        provision(self.api, c, lambda: None)
        for path in ('/api/config-profiles', '/api/nodes', '/api/hosts', '/api/internal-squads'):
            self.assertEqual(len(Handler.records[path]), 1)


if __name__ == '__main__':
    unittest.main()
