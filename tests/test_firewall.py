import unittest

from godov.config import Config
from godov.system import firewall_rules


class FirewallTests(unittest.TestCase):
    def test_allows_only_panel_source_and_loopback_and_drops_the_rest(self):
        c = Config(panel_ip='203.0.113.8', firewall_networks=['172.19.0.0/16'])
        rules = firewall_rules(c, 4)
        self.assertIn('-i lo -j ACCEPT', rules)
        self.assertIn('-s 203.0.113.8/32 -j ACCEPT', rules)
        self.assertIn('-s 172.19.0.0/16 -j ACCEPT', rules)
        self.assertNotIn('0.0.0.0/0', rules)
        self.assertTrue(rules.endswith('-A G0D0V_NODE -j DROP\nCOMMIT\n'))

    def test_ipv6_has_its_own_allowlist(self):
        c = Config(panel_ip='2001:db8::1', firewall_networks=['172.19.0.0/16'])
        self.assertIn('-s 2001:db8::1/128 -j ACCEPT', firewall_rules(c, 6))
        self.assertNotIn('172.19.', firewall_rules(c, 6))
        self.assertNotIn('2001:db8', firewall_rules(c, 4))

    def test_untrusted_ip_cannot_inject_iptables_statements(self):
        with self.assertRaises(ValueError):
            firewall_rules(Config(panel_ip='203.0.113.8\n-A INPUT -j ACCEPT'), 4)


if __name__ == '__main__':
    unittest.main()
