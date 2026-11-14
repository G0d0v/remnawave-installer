import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from godov.config import Config, load, save
from godov.operations import renew_tokens
from godov.system import Stack


class TokenRenewalTests(unittest.TestCase):
    def test_success_updates_connection_and_credentials_without_changing_node_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'stack'
            c = Config(mode='panel', panel_domain='panel.example.com', sub_domain='sub.example.com',
                       email='admin@example.com', api_token='old-manager', bot_token='old-bot')
            save(root, c)

            def issue(api, candidate, checkpoint):
                candidate.api_token, candidate.bot_token = 'new-manager', 'new-bot'
                checkpoint()

            with patch('godov.operations.backup_stack', return_value=root / 'snapshot.tar.gz'), \
                    patch('godov.operations.bootstrap_panel', side_effect=issue):
                renew_tokens(Stack(root), '')
            restored = load(root)
            self.assertEqual(restored.api_token, 'new-manager')
            self.assertEqual(restored.bot_token, 'new-bot')
            self.assertEqual(restored.install_id, c.install_id)
            self.assertIn('new-bot', (root / 'credentials.txt').read_text())

    def test_failed_issuance_preserves_existing_tokens_even_after_partial_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'stack'
            c = Config(mode='panel', panel_domain='panel.example.com', sub_domain='sub.example.com',
                       email='admin@example.com', api_token='old-manager', bot_token='old-bot')
            save(root, c)

            def issue(api, candidate, checkpoint):
                candidate.api_token = 'partially-issued'
                checkpoint()
                raise RuntimeError('network unavailable')

            with patch('godov.operations.backup_stack', return_value=root / 'snapshot.tar.gz'), \
                    patch('godov.operations.bootstrap_panel', side_effect=issue), self.assertRaises(RuntimeError):
                renew_tokens(Stack(root), '')
            self.assertEqual(load(root).api_token, 'old-manager')
            self.assertEqual(load(root).bot_token, 'old-bot')


if __name__ == '__main__':
    unittest.main()
