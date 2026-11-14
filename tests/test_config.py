import tempfile
import unittest
from pathlib import Path

from godov.config import Config, domain, load, save


class ConfigTests(unittest.TestCase):
    def test_domains_cannot_inject_caddy_or_shell(self):
        for value in ('x.com\nadmin off', 'x.com;id', 'https://x.com', '*.x.com', '-a.com', 'x.com/path'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                domain(value)

    def test_validates_and_normalizes_domains(self):
        self.assertEqual(domain('Panel.Example.COM.'), 'panel.example.com')
        self.assertEqual(domain('панель.пример.рф'), 'xn--80aksgi6f.xn--e1afmkfd.xn--p1ai')

    def test_combined_requires_selfsteal_and_unique_domains(self):
        c = Config(mode='panel-node', panel_domain='panel.example.com', sub_domain='sub.example.com',
                   node_domain='node.example.com', email='admin@example.com')
        c.validate()
        c.selfsteal = False
        with self.assertRaises(ValueError):
            c.validate()
        c.selfsteal = True
        c.sub_domain = c.panel_domain
        with self.assertRaises(ValueError):
            c.validate()

    def test_remote_node_requires_explicit_panel_ip_for_firewall(self):
        c = Config(mode='attach', panel_url='https://panel.example.com', api_token='opaque',
                   node_domain='node.example.com', node_address='node.example.com', email='admin@example.com')
        with self.assertRaises(ValueError):
            c.validate()
        c.panel_ip = '203.0.113.2'
        c.validate()

    def test_state_roundtrip_preserves_secrets_and_install_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            c = Config(mode='panel', panel_domain='panel.example.com', sub_domain='sub.example.com',
                       email='admin@example.com')
            root = Path(temp) / 'stack'
            save(root, c)
            restored = load(root)
            self.assertEqual(c.install_id, restored.install_id)
            self.assertEqual(c.db_password, restored.db_password)
            self.assertGreaterEqual(len(restored.admin_password), 24)
            self.assertEqual(restored.panel_image, 'remnawave/backend:3.4.11')

    def test_refuses_to_adopt_nonempty_unmanaged_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'existing.txt').write_text('keep', encoding='utf-8')
            with self.assertRaises(ValueError):
                save(root, Config())
            self.assertEqual((root / 'existing.txt').read_text(), 'keep')

    def test_rejects_dangerous_or_unpinned_image_references(self):
        c = Config(mode='panel', panel_domain='panel.example.com', sub_domain='sub.example.com',
                   email='admin@example.com', panel_image='remnawave/backend:latest')
        with self.assertRaises(ValueError):
            c.validate()


if __name__ == '__main__':
    unittest.main()
