import unittest
from app import infrastructure as i, linux_agent


class AgentMetricsTests(unittest.TestCase):
    def test_windows_and_old_agent_payloads(self):
        base=dict(hostname='host',cpu_count=4,memory_percent=30,uptime_seconds=300)
        old=i.Metrics(platform='linux',**base)
        self.assertIsNone(old.temperature_c)
        new=i.Metrics(platform='windows',**base,cpu_percent=20,network_rx_bps=1024,
                      processes=[{'name':'critical','state':'inactive'}],reboot_pending=True,
                      critical_events_5m=2)
        self.assertEqual(new.network_rx_bps,1024)
        server={'cpu_threshold':90,'memory_threshold':90,'disk_threshold':90}
        self.assertIn('Service critical: inactive',i.violations(server,new))

    def test_linux_collect(self):
        if not __import__('sys').platform.startswith('linux'):
            self.skipTest('Linux /proc required')
        previous={}
        payload=linux_agent.collect({'mounts':['/']},previous)
        self.assertEqual(i.Metrics.model_validate(payload).platform,'linux')
        payload=linux_agent.collect({'mounts':['/']},previous)
        self.assertIn('network_rx_bps',payload)
        i.Metrics.model_validate(payload)


if __name__=='__main__':
    unittest.main()
