from pathlib import Path
import importlib.util
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rsshub_trial', Path(__file__).resolve().parents[1] / 'deploy/setup_rsshub_trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def result(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, '')


class RsshubTrialTests(unittest.TestCase):
    def test_runtime_is_private_bounded_and_has_no_credentials(self):
        args = trial.start_args()
        for flag, value in (('--memory=768m', None), ('--memory-swap=768m', None),
                            ('--cpus=0.5', None), ('--restart=no', None), ('--read-only', None),
                            ('--log-driver=none', None), ('-p', '127.0.0.1:1200:1200')):
            self.assertIn(flag, args)
            if value:
                self.assertEqual(args[args.index(flag) + 1], value)
        self.assertRegex(args[-1], r'^ghcr.io/diygod/rsshub@sha256:[a-f0-9]{64}$')
        self.assertNotIn('TWITTER_AUTH_TOKEN', ' '.join(args))
        self.assertNotIn('--env-file', args)
        self.assertIn('unix:///var/run/docker.sock', trial.DOCKER)

    def test_unhealthy_bot_or_low_resources_stop_before_install(self):
        with patch.object(trial, 'bot_ok', return_value=False), patch.object(trial, 'ensure_docker') as install:
            with self.assertRaises(trial.TrialError):
                trial.prepare()
            install.assert_not_called()
        with patch.object(trial, 'bot_ok', return_value=True), patch.object(trial, 'memory_available', return_value=100 * trial.MIB), patch.object(trial, 'ensure_docker') as install:
            with self.assertRaises(trial.TrialError):
                trial.prepare()
            install.assert_not_called()

    def test_never_removes_an_unowned_same_name_container(self):
        with patch.object(trial, 'docker', return_value=result(trial.NAME + '|other')):
            with self.assertRaises(trial.TrialError):
                trial.owned_exists()
        with patch.object(trial, 'docker', return_value=result(trial.NAME + '|rsshub-trial')):
            self.assertTrue(trial.owned_exists())

    def test_failed_bot_health_after_start_removes_only_trial(self):
        with patch.object(trial, 'check_budget'), patch.object(trial, 'check_free_port'), patch.object(trial, 'ensure_docker'), patch.object(trial, 'owned_exists', side_effect=[False, True]), patch.object(trial, 'docker', return_value=result()) as docker, patch.object(trial, 'bot_ok', return_value=False):
            with self.assertRaises(trial.TrialError):
                trial.prepare()
            docker.assert_any_call('rm', '-f', trial.NAME, required=False)
            self.assertFalse(any('coinw.service' in str(call) for call in docker.call_args_list))

    def test_command_error_does_not_print_arbitrary_stderr_or_args(self):
        with patch.object(trial.subprocess, 'run', return_value=result('private response', 1)):
            with self.assertRaises(trial.TrialError) as error:
                trial.run(['command', 'private-argument'])
            self.assertNotIn('private', str(error.exception))

    def test_health_check_requires_real_health_response(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return self.body
        for body, expected in ((b'ok', True), (b'<html>login</html>', False)):
            with self.subTest(body=body), patch.object(trial.HTTP, 'open', return_value=Response(body)):
                self.assertEqual(trial.rsshub_ok(), expected)


if __name__ == '__main__':
    unittest.main()
