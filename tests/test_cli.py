import contextlib
import io
import unittest

from godov.cli import main, parser


class CLITests(unittest.TestCase):
    def test_previews_do_not_require_root_or_mutate_any_server(self):
        for name in ('menu', 'install', 'plan'):
            with self.subTest(name=name), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--preview', name]), 0)
                self.assertIn('1.0.0', output.getvalue())
                self.assertGreater(len(output.getvalue()), 300)

    def test_api_tokens_are_not_accepted_as_process_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['install', '--api-token', 'do-not-accept'])


if __name__ == '__main__':
    unittest.main()
