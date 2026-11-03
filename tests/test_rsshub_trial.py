from pathlib import Path
from datetime import datetime, timezone
import getpass
import importlib.util
import io
import subprocess
import tempfile
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

    def test_login_never_places_cookie_in_docker_arguments(self):
        args = trial.start_args(authenticated=True)
        self.assertEqual(args[args.index('--env-file') + 1], str(trial.PRIVATE / 'x.env'))
        self.assertNotIn('TWITTER_AUTH_TOKEN', ' '.join(args))

    def test_hidden_input_refuses_echo_fallback_and_invalid_values(self):
        with patch.object(trial.sys.stdin, 'isatty', return_value=False), patch.object(trial.getpass, 'getpass') as prompt:
            with self.assertRaises(trial.TrialError):
                trial.hidden_token()
            prompt.assert_not_called()
        for value in ('not-a-cookie', 'BotFather:token', 'a' * 40 + '\nNODE_OPTIONS=bad'):
            with patch.object(trial.sys.stdin, 'isatty', return_value=True), patch.object(trial.getpass, 'getpass', return_value=value):
                with self.assertRaises(trial.TrialError) as error:
                    trial.hidden_token()
                self.assertNotIn(value, str(error.exception))
        with patch.object(trial.sys.stdin, 'isatty', return_value=True), patch.object(trial.getpass, 'getpass', side_effect=getpass.GetPassWarning('private')):
            with self.assertRaises(trial.TrialError):
                trial.hidden_token()

    def test_credentials_are_written_atomically_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(trial, 'PRIVATE', Path(folder)), patch.object(trial, 'private_dir'):
            trial.write_private('x.env', 'synthetic-test\n')
            target = Path(folder) / 'x.env'
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.read_text(), 'synthetic-test\n')
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_check_cooldown_stops_repeated_requests(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(trial, 'PRIVATE', Path(folder)), patch.object(trial, 'private_dir'), patch.object(trial.time, 'time', return_value=10000):
            (Path(folder) / 'last-check').write_text('9900')
            with self.assertRaises(trial.TrialError):
                trial.check_cooldown()
            (Path(folder) / 'last-check').write_text('9000')
            trial.check_cooldown()

    def test_feed_requires_account_content_and_recent_dates(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        body = b'''<rss><channel><item><link>https://x.com/CoinWOfficial/status/123</link>
        <pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate><description><![CDATA[
        <script>hidden</script><p>Community news</p><img src="https://pbs.twimg.com/p.jpg"/>
        ]]></description></item></channel></rss>'''
        report = trial.inspect_feed(body, now)
        self.assertEqual(report['recent'], 1)
        self.assertEqual(report['samples'][0]['images'], 1)
        self.assertNotIn('hidden', report['samples'][0]['preview'])
        for invalid in (body.replace(b'CoinWOfficial', b'OtherAccount'), body.replace(b'2026 10:', b'2026 99:'), b'<html>sign in</html>', b'<!DOCTYPE rss><rss/>'):
            with self.assertRaises(trial.TrialError):
                trial.inspect_feed(invalid, now)
        self.assertEqual(trial.inspect_feed(body.replace(b'2026 10:', b'2020 10:'), now)['recent'], 0)

    def test_error_response_body_never_reaches_console(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as folder, patch.object(trial, 'PRIVATE', Path(folder)), patch.object(trial, 'private_dir'), patch.object(trial, 'owned_exists', return_value=True), patch.object(trial, 'rsshub_ok', return_value=True), patch.object(trial, 'bot_ok', return_value=True), patch.object(trial.HTTP, 'open', side_effect=urllib.error.HTTPError(trial.FEED_URL, 500, 'sensitive', {}, io.BytesIO(b'private-cookie'))):
            (Path(folder) / 'x.env').write_text('synthetic')
            with self.assertRaises(trial.TrialError) as error:
                trial.check_feed()
            self.assertIn('500', str(error.exception))
            self.assertNotIn('private', str(error.exception))
            self.assertNotIn('sensitive', str(error.exception))


if __name__ == '__main__':
    unittest.main()
