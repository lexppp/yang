import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('forward', Path(__file__).resolve().parents[1] / 'deploy/x_to_telegram.py')
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)
installer_spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'deploy/install_x_forward.py')
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)


def post(pid='2', published=101, text='新帖 日本語 English 😀', images=None):
    return dict(id=pid, published=published, text=text, images=images or [],
                url='https://x.com/CoinWOfficial/status/' + pid)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = forward.connect(Path(self.directory.name) / 'state.sqlite3')
        forward.initialize(self.db, [post('1', 90)], 100)
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def send(self, method, data):
        self.calls.append((method, data))
        return {'message_id': len(self.calls)}

    def test_only_new_posts_deduplicated_across_restart(self):
        posts = [post('1', 90), post('3', 99), post()]
        forward.cycle(self.db, posts, self.send, 110)
        self.db.close()
        self.db = forward.connect(Path(self.directory.name) / 'state.sqlite3')
        self.assertFalse(forward.initialize(self.db, posts, 120))
        forward.cycle(self.db, posts, self.send, 120)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]['text'], post()['text'])
        self.assertEqual(self.calls[0][1]['reply_markup']['inline_keyboard'][0][0]['url'], post()['url'])

    def test_photo_caption_and_multiple_images(self):
        images = ['https://pbs.twimg.com/media/a.jpg', 'https://pbs.twimg.com/media/b.jpg']
        forward.cycle(self.db, [post(images=images)], self.send, 110)
        self.assertEqual([call[0] for call in self.calls], ['sendPhoto', 'sendPhoto'])
        self.assertEqual(self.calls[0][1]['caption'], post()['text'])
        self.assertNotIn('caption', self.calls[1][1])

    def test_long_unicode_text_preserved_before_photos(self):
        text = '😀日本語\n' * 2000
        steps = forward.plan(post(text=text, images=['https://pbs.twimg.com/media/a.jpg']))
        self.assertEqual(''.join(data['text'] for method, data in steps if method == 'sendMessage'), text)
        self.assertTrue(all(len(data['text'].encode('utf-16-le')) // 2 <= 4096 for method, data in steps if method == 'sendMessage'))
        self.assertEqual(steps[-1][0], 'sendPhoto')

    def test_timeout_does_not_resend_on_next_run(self):
        def timeout(method, data):
            self.calls.append(method)
            raise forward.TelegramFailure('uncertain')
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [post()], timeout, 110)
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [post()], self.send, 120)
        self.assertEqual(len(self.calls), 1)

    def test_process_death_marker_is_not_resent(self):
        with self.db:
            self.db.execute('INSERT INTO posts VALUES(?,?,?)', ('2', json.dumps(post()), 'pending'))
            self.db.execute("INSERT INTO deliveries VALUES('2',0,'sending',NULL)")
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [], self.send, 120)
        self.assertEqual(self.calls, [])

    def test_rate_limit_retries_only_unsent_part_after_delay(self):
        images = ['https://pbs.twimg.com/media/a.jpg', 'https://pbs.twimg.com/media/b.jpg']
        def rate_limit(method, data):
            if len(self.calls) == 1:
                raise forward.TelegramFailure('rate_limited', 900)
            return self.send(method, data)
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [post(images=images)], rate_limit, 110)
        forward.cycle(self.db, [], self.send, 1000)
        self.assertEqual(len(self.calls), 1)
        forward.cycle(self.db, [], self.send, 1011)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1][1]['photo'], images[1])

    def test_permanent_rejection_requires_inspection(self):
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [post()], lambda *_: (_ for _ in ()).throw(forward.TelegramFailure('rejected')), 110)
        with self.assertRaises(forward.Failure):
            forward.cycle(self.db, [], self.send, 120)
        self.assertFalse(self.calls)

    def test_parser_checks_source_dates_and_media_hosts(self):
        body = b'''<rss><channel><item><link>https://x.com/CoinWOfficial/status/123</link>
        <pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate>
        <description><![CDATA[<p>Hello</p><img src="https://pbs.twimg.com/media/a.jpg"/><img src="http://127.0.0.1/secret"/><script>hidden</script>]]></description>
        </item><item><link>https://x.com/Other/status/234</link></item></channel></rss>'''
        posts = forward.parse_feed(body)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['text'], 'Hello')
        self.assertEqual(posts[0]['images'], ['https://pbs.twimg.com/media/a.jpg'])
        with self.assertRaises(forward.Failure):
            forward.parse_feed(b'<!DOCTYPE rss><rss/>')
        with self.assertRaises(forward.Failure):
            forward.parse_feed(body.replace(b'+0000', b'invalid'))

    def test_preflight_requires_expected_bot_channel_and_rights(self):
        responses = {'getMe': {'id': 1, 'username': forward.BOT},
                     'getChat': {'id': -100, 'type': 'channel', 'username': forward.CHANNEL[1:]},
                     'getChatMember': {'status': 'administrator', 'can_post_messages': True}}
        forward.preflight(lambda method, data: responses[method])
        responses['getChatMember']['can_post_messages'] = False
        with self.assertRaises(forward.Failure):
            forward.preflight(lambda method, data: responses[method])

    def test_network_exception_does_not_expose_token(self):
        with patch.object(forward.urllib.request, 'urlopen', side_effect=OSError('https://api.telegram.org/bot123:secret')):
            with self.assertRaises(forward.TelegramFailure) as error:
                forward.telegram('123:secret', 'sendMessage', {})
        self.assertNotIn('secret', str(error.exception))


class InstallerTests(unittest.TestCase):
    def test_units_use_existing_env_private_state_and_ten_minute_timer(self):
        self.assertIn('EnvironmentFile=/etc/coinw/coinw.env', installer.SERVICE)
        self.assertIn('ReadWritePaths=/var/lib/coinw-x-forward', installer.SERVICE)
        self.assertIn('OnUnitInactiveSec=10min', installer.TIMER)
        self.assertNotIn('getUpdates', installer.SERVICE)

    def test_installs_without_stopping_main_bot_or_recreating_rsshub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            state = Path(directory) / 'state'
            units = Path(directory) / 'units'
            units.mkdir()
            commands = []
            def run(args, **kwargs):
                commands.append(args)
                return 'rsshub-trial' if 'inspect' in args else 'ok'
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'STATE', state), patch.object(installer, 'UNITS', units), patch.object(installer, 'health'), patch.object(installer, 'existing_token', return_value='123:private'), patch.object(installer.Path, 'is_file', return_value=True), patch.object(installer, 'run', side_effect=run), patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3)):
                installer.install()
                # Repeated installation keeps DB initialization idempotent in worker.
                installer.install()
            self.assertEqual((root / 'x_to_telegram.py').read_bytes(), Path(forward.__file__).read_bytes())
            self.assertEqual((units / 'coinw-x-forward.timer').read_text(), installer.TIMER)
            self.assertFalse(any(cmd[:2] == ['systemctl', 'stop'] and cmd[-1] in ('coinw', 'coinw.service') for cmd in commands))
            self.assertFalse(any('rm' in cmd or 'getUpdates' in cmd for cmd in commands))

    def test_foreign_unit_blocks_install_before_any_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            units = Path(directory)
            (units / 'coinw-x-forward.service').write_text('foreign service')
            with patch.object(installer, 'UNITS', units), patch.object(installer, 'health') as health:
                with self.assertRaises(ValueError):
                    installer.install()
                health.assert_not_called()


if __name__ == '__main__':
    unittest.main()
