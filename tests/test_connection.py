import json
import tempfile
import unittest
from pathlib import Path

from godov.bot import export_connection, read_connection
from godov.config import Config


class ConnectionTests(unittest.TestCase):
    def test_export_contains_only_connection_fields_and_no_admin_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            c = Config(panel_url='https://panel.example.com', bot_token='bot-secret',
                       sub_domain='sub.example.com', resources={'squad': '25ef3500-289e-456a-a715-a6bbf4d8c5a1'})
            path = Path(temp) / 'connection.json'
            export_connection(c, path)
            text = path.read_text()
            self.assertNotIn(c.admin_password, text)
            self.assertNotIn(c.db_password, text)
            remote = read_connection(path)
            self.assertEqual(remote.bot_token, c.bot_token)
            self.assertEqual(remote.install_id, c.install_id)
            self.assertEqual(remote.resources['squad'], c.resources['squad'])

    def test_rejects_http_connection_or_invalid_squad_before_any_network_call(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'connection.json'
            data = {'install_id': 'aabbcc112233', 'panel_url': 'http://panel.example.com',
                    'token': 'test-token', 'squad': 'invalid', 'sub_domain': ''}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read_connection(path)


if __name__ == '__main__':
    unittest.main()
