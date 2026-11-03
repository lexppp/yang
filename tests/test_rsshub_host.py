from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('rsshub_host',Path(__file__).resolve().parents[1]/'deploy/check_rsshub_host.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)


class HostAssessmentTests(unittest.TestCase):
    def test_capacity_is_only_a_trial_budget_not_a_deployment_claim(self):
        result=host.assessment(1200*host.MIB,10*host.GIB,True,False,True)
        self.assertTrue(result['resource_budget_ok']);self.assertIn('仍需验证',result['verdict'])
        self.assertFalse(result['trial_plan']['browser_service']);self.assertFalse(result['trial_plan']['redis_service'])
        self.assertEqual(result['trial_plan']['bind'],'127.0.0.1:1200')
    def test_low_memory_or_disk_do_not_receive_a_ready_verdict(self):
        for memory,disk in ((300*host.MIB,10*host.GIB),(1200*host.MIB,host.GIB),(None,10*host.GIB)):
            with self.subTest(memory=memory,disk=disk):
                result=host.assessment(memory,disk,True,False,True)
                self.assertFalse(result['resource_budget_ok']);self.assertIn('尚不能确认',result['verdict'])
    def test_missing_docker_busy_port_and_unhealthy_bot_are_explicit(self):
        result=host.assessment(1200*host.MIB,10*host.GIB,False,True,False)
        self.assertEqual(len(result['issues']),3);self.assertIn('尚不能确认',result['verdict'])
        no_docker=host.assessment(1200*host.MIB,10*host.GIB,False,False,True)
        self.assertTrue(no_docker['resource_budget_ok']);self.assertIn('尚不能确认',no_docker['verdict'])
        cpu_busy=host.assessment(1200*host.MIB,10*host.GIB,True,False,True,True)
        self.assertIn('尚不能确认',cpu_busy['verdict'])
    def test_memory_reader_only_returns_resource_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'meminfo'
            path.write_text('MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\nSwapTotal: 0 kB\nOther: 99 kB\n')
            result=host.memory_info(path)
            self.assertEqual(result['MemAvailable'],1024000*1024);self.assertNotIn('Other',result)
    def test_command_failure_never_echoes_daemon_error_or_configuration(self):
        class Result:
            returncode=1
            stdout='private configuration'
            stderr='sensitive daemon configuration'
        with patch('subprocess.run',return_value=Result()):self.assertIsNone(host.command(['docker','version']))


if __name__=='__main__':unittest.main()
