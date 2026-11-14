"""Disposable Ubuntu runner test: real panel, node, TLS fallback and VLESS traffic.

Only ACME is replaced with Caddy's internal CA. Never run on a production host.
"""

import json
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from godov.api import API
from godov.backup import backup_stack, restore_stack
from godov.config import Config
from godov.render import render
from godov.system import Stack, remove_firewall, run


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise SystemExit('Only permitted on a disposable GitHub Actions runner as root.')
    root = pathlib.Path('/opt/godov-remnawave')
    if root.exists():
        raise SystemExit('The runner already contains a deployment; refusing to adopt it.')
    c = Config(panel_domain='panel.example.test', sub_domain='sub.example.test',
               node_domain='node.example.test', node_address='node.example.test', email='ci@example.test')
    stack = Stack(root)
    original_hosts = pathlib.Path('/etc/hosts').read_text()
    pathlib.Path('/etc/hosts').write_text(original_hosts + '\n127.0.0.1 panel.example.test sub.example.test node.example.test\n')
    internal = lambda directory, config: render(directory, config, internal_tls=True)
    try:
        with patch('godov.system.render', internal), patch('godov.backup.render', internal):
            stack.install(c, skip_dns=True)
            print('PASS: full installation and authenticated node connection', flush=True)
            api = API('http://127.0.0.1:3000', c.bot_token, allow_local=True)
            user = api.post('/api/users', {
                'username': 'godov_ci', 'status': 'ACTIVE', 'trafficLimitBytes': 0,
                'trafficLimitStrategy': 'NO_RESET', 'activeInternalSquads': [c.resources['squad']],
                'expireAt': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace('+00:00', 'Z'),
            })
            for secret in (c.admin_password, c.api_token, c.bot_token, c.node_secret, c.private_key):
                print('::add-mask::' + secret, flush=True)
            run(['curl', '--fail', '--silent', '--show-error', '--insecure',
                 'https://panel.example.test/api/auth/status'], timeout=30)
            page = run(['curl', '--fail', '--silent', '--show-error', '--insecure',
                        'https://node.example.test'], timeout=30).stdout
            assert 'FORM / STUDIO' in page, 'Selfsteal did not serve the actual decoy site'
            hidden = run(['curl', '--silent', '--insecure', '-o', '/dev/null', '-w', '%{http_code}',
                          'https://sub.example.test/api/auth/status']).stdout
            assert hidden == '404', 'Admin API was exposed on subscription domain'
            subscription = run(['curl', '--fail', '--silent', '--show-error', '--insecure',
                                '-H', 'User-Agent: v2rayNG/1.10',
                                'https://sub.example.test/api/sub/' + user['shortUuid']], timeout=30).stdout
            assert subscription, 'Subscription is empty'
            print('PASS: HTTPS panel, selfsteal site and isolated subscription domain', flush=True)
            client = {
                'log': {'loglevel': 'warning'},
                'inbounds': [{'listen': '127.0.0.1', 'port': 10808, 'protocol': 'socks', 'settings': {'udp': False}}],
                'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [
                    {'address': '127.0.0.1', 'port': 443,
                     'users': [{'id': user['vlessUuid'], 'encryption': 'none', 'flow': 'xtls-rprx-vision'}]}]},
                    'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                        'serverName': c.node_domain, 'fingerprint': 'chrome',
                        'password': c.public_key, 'shortId': c.short_id}}}],
            }
            client_path = root / 'client.json'
            client_path.write_text(json.dumps(client))
            run(['docker', 'run', '-d', '--name', 'godov-ci-client', '--network', 'host',
                 '-v', str(client_path) + ':/client.json:ro', '--entrypoint', '/usr/local/bin/xray',
                 c.node_image, 'run', '-config', '/client.json'])
            time.sleep(8)
            response = run(['curl', '--fail', '--silent', '--show-error', '--socks5-hostname',
                            '127.0.0.1:10808', '--max-time', '45', 'https://example.com'], timeout=60).stdout
            assert 'Example Domain' in response, 'No HTTP response through VLESS/Reality'
            print('PASS: real VLESS/Reality/Vision traffic through the installed node', flush=True)
            counts = {path: len(stack.api(c).get(path)) for path in ('/api/nodes', '/api/hosts')}
            stack.install(c, skip_dns=True)
            assert counts == {path: len(stack.api(c).get(path)) for path in counts}, 'Resume created duplicate resources'
            print('PASS: repeated installation resumes without duplicate nodes or hosts', flush=True)
            snapshot = backup_stack(stack)
            api.patch('/api/users', {'id': user['id'], 'status': 'DISABLED'})
            restore_stack(stack, snapshot)
            restored = api.get('/api/users/' + str(user['id']))
            assert restored['status'] == 'ACTIVE', 'PostgreSQL state was not restored'
            print('PASS: real pg_dump/pg_restore and node reconnect', flush=True)
    except Exception:
        for secret in (c.admin_password, c.api_token, c.bot_token, c.node_secret, c.private_key,
                       c.db_password, c.app_secret, c.metrics_password, c.webhook_secret):
            if secret:
                print('::add-mask::' + secret, flush=True)
        if c.resources.get('node'):
            try:
                status = stack.api(c).get('/api/nodes/' + c.resources['node'])
                print('Node diagnostic:', json.dumps({key: status.get(key) for key in
                      ('isConnected', 'isConnecting', 'xrayUptime', 'lastStatusMessage')}), flush=True)
            except Exception:
                print('Node diagnostic unavailable', flush=True)
        raise
    finally:
        # Error diagnostics contain no env files, dump files, credentials or raw API responses.
        print(stack.compose('ps', '--all', check=False).stdout, flush=True)
        run(['docker', 'rm', '-f', 'godov-ci-client'], check=False)
        if (root / 'compose.yaml').exists():
            stack.compose('down', '--volumes', check=False)
        remove_firewall(c)
        pathlib.Path('/etc/hosts').write_text(original_hosts)


if __name__ == '__main__':
    main()
