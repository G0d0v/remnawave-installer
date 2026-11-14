import unittest

from godov.config import Config
from godov.render import compose, caddyfile, xray_profile


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.c = Config(mode='panel-node', panel_domain='panel.example.com', sub_domain='sub.example.com',
                        node_domain='node.example.com', email='admin@example.com',
                        private_key='test-private-key', public_key='test-public-key', node_secret='test-secret')

    def test_databases_have_no_public_port_and_panel_is_loopback_only(self):
        services = compose(self.c)['services']
        self.assertNotIn('ports', services['db'])
        self.assertNotIn('ports', services['redis'])
        self.assertEqual(services['panel']['ports'][0], '127.0.0.1:3000:3000')
        self.assertEqual(services['node']['network_mode'], 'host')
        self.assertIn('host.docker.internal:host-gateway', services['panel']['extra_hosts'])

    def test_selfsteal_reserves_443_for_xray_and_binds_caddy_to_loopback(self):
        caddy = caddyfile(self.c)
        self.assertIn('https_port 8443', caddy)
        self.assertIn('bind 127.0.0.1', caddy)
        self.assertIn('disable_tlsalpn_challenge', caddy)
        self.assertIn('proxy_protocol', caddy)
        inbound = xray_profile(self.c)['inbounds'][0]
        self.assertEqual(inbound['port'], 443)
        self.assertEqual(inbound['streamSettings']['realitySettings']['target'], '127.0.0.1:8443')
        self.assertEqual(inbound['streamSettings']['realitySettings']['xver'], 1)

    def test_subscription_domain_cannot_reach_panel_admin_api(self):
        caddy = caddyfile(self.c)
        sub = caddy.split('https://sub.example.com {', 1)[1]
        self.assertIn('@subscription path /api/sub/*', sub)
        self.assertIn('respond 404', sub)

    def test_panel_only_uses_443_without_node(self):
        self.c.mode = 'panel'
        services = compose(self.c)['services']
        self.assertNotIn('node', services)
        self.assertNotIn('https_port 8443', caddyfile(self.c))

    def test_caddy_entrypoint_supports_maintenance_subcommands(self):
        self.assertEqual(compose(self.c)['services']['caddy']['entrypoint'], ['caddy'])

    def test_external_reality_node_has_no_panel_or_caddy(self):
        self.c.mode = 'node'
        self.c.selfsteal = False
        self.c.reality_domain = 'www.microsoft.com'
        services = compose(self.c)['services']
        self.assertEqual(set(services), {'node'})
        reality = xray_profile(self.c)['inbounds'][0]['streamSettings']['realitySettings']
        self.assertEqual(reality['target'], 'www.microsoft.com:443')
        self.assertEqual(reality['xver'], 0)


if __name__ == '__main__':
    unittest.main()
