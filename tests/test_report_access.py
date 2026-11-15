# SPDX-License-Identifier: AGPL-3.0-only
import ipaddress
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from batch_culling import report_access_allowed


class ReportAccessTests(unittest.TestCase):
    def test_all_sources_is_read_only_not_all_hosts_or_cross_origin(self):
        hosts = {"127.0.0.1", "localhost", "192.168.10.2", "report.example"}
        network = ipaddress.ip_network("192.168.10.0/24")
        def allowed(remote, host="192.168.10.2:5174", origin=None, all_sources=False, write=False):
            return report_access_allowed(remote, host, origin, 5174, hosts, network, all_sources, write)
        self.assertFalse(allowed("203.0.113.8"))
        self.assertTrue(allowed("203.0.113.8", all_sources=True))
        self.assertFalse(allowed("203.0.113.8", all_sources=True, write=True))
        self.assertFalse(allowed("203.0.113.8", host="attacker.example:5174", all_sources=True))
        self.assertFalse(allowed("203.0.113.8", origin="https://attacker.example", all_sources=True))
        self.assertFalse(allowed("203.0.113.8", host="user@192.168.10.2:5174", all_sources=True))
        self.assertFalse(allowed("203.0.113.8", host="192.168.10.2:9999", all_sources=True))
        self.assertTrue(allowed("192.168.10.50"))
        self.assertFalse(allowed("192.168.11.50"))
        self.assertTrue(allowed("127.0.0.1", host="127.0.0.1:5174", write=True))
        self.assertFalse(allowed("127.0.0.1", host="192.168.10.2:5174", write=True))


if __name__ == "__main__":
    unittest.main()
