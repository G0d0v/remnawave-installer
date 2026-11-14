"""Validate all modes with the actual Compose and Caddy parsers on Linux."""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from godov.config import Config
from godov.render import render
from godov.system import Stack, run


def main():
    for mode, steal in (('panel-node', True), ('panel', True), ('node', True), ('node', False), ('attach', True)):
        c = Config(mode=mode, selfsteal=steal, panel_domain='panel.example.com', sub_domain='sub.example.com',
                   node_domain='node.example.com', node_address='node.example.com', email='ci@example.com',
                   panel_url='https://panel.example.com', panel_ip='203.0.113.8',
                   api_token='template-validation-token', node_secret='template-validation-secret')
        c.validate()
        with tempfile.TemporaryDirectory(prefix='godov-templates-') as temp:
            root = pathlib.Path(temp)
            render(root, c)
            Stack(root).compose('config', '--quiet')
            if c.has_caddy:
                run(['docker', 'run', '--rm', '-v', str(root / 'Caddyfile') + ':/etc/caddy/Caddyfile:ro',
                     c.caddy_image, 'adapt', '--config', '/etc/caddy/Caddyfile'])
        print(f'PASS: Compose and Caddy parsers · {mode}, selfsteal={steal}', flush=True)


if __name__ == '__main__':
    main()
