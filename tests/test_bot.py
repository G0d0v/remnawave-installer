import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from godov.bot import configure_database


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'users.db'
        with closing(sqlite3.connect(self.db)) as con, con:
            con.executescript('''
                CREATE TABLE xui_hosts (host_name TEXT PRIMARY KEY, host_url TEXT, host_username TEXT,
                    host_pass TEXT, host_inbound_id INTEGER, subscription_url TEXT,
                    remnawave_base_url TEXT, remnawave_api_token TEXT, squad_uuid TEXT);
                CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE users (id INTEGER PRIMARY KEY, balance INTEGER);
                INSERT INTO users VALUES (100, 999);
            ''')
        self.payload = {'name': 'G0d0v-abcdef', 'url': 'https://panel.example.com', 'token': 'secret',
                        'squad': 'squad-uuid', 'subscription': 'https://sub.example.com/api/sub'}

    def tearDown(self):
        self.temp.cleanup()

    def test_binds_bot_with_backup_and_preserves_users_and_payments(self):
        backup = configure_database(self.db, self.payload)
        self.assertTrue(Path(backup).exists())
        with closing(sqlite3.connect(self.db)) as con, con:
            row = con.execute('SELECT remnawave_base_url, squad_uuid FROM xui_hosts').fetchone()
            self.assertEqual(row, ('https://panel.example.com', 'squad-uuid'))
            self.assertEqual(con.execute('SELECT balance FROM users WHERE id=100').fetchone()[0], 999)
        configure_database(self.db, self.payload)
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM xui_hosts').fetchone()[0], 1)

    def test_does_not_overwrite_other_hosts_or_global_settings(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('INSERT INTO bot_settings VALUES (?, ?)', ('remnawave_api_token', 'old-secret'))
            con.execute('INSERT INTO xui_hosts(host_name,remnawave_base_url,squad_uuid) VALUES (?,?,?)',
                        ('Existing', 'https://old.example.com', 'old-squad'))
        configure_database(self.db, self.payload)
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute('SELECT value FROM bot_settings').fetchone()[0], 'old-secret')
            self.assertEqual(con.execute('SELECT squad_uuid FROM xui_hosts WHERE host_name=?', ('Existing',)).fetchone()[0], 'old-squad')

    def test_refuses_collision_with_existing_host(self):
        configure_database(self.db, self.payload)
        self.payload['url'] = 'https://different.example.com'
        self.payload['squad'] = 'different-squad'
        with self.assertRaises(ValueError):
            configure_database(self.db, self.payload)

    def test_rebind_after_panel_domain_change_keeps_the_same_squad(self):
        configure_database(self.db, self.payload)
        self.payload['url'] = 'https://new-panel.example.com'
        configure_database(self.db, self.payload)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT host_url,remnawave_base_url,squad_uuid FROM xui_hosts').fetchone(),
                             ('https://new-panel.example.com', 'https://new-panel.example.com', 'squad-uuid'))


if __name__ == '__main__':
    unittest.main()
