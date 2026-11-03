"""Read-only, credential-free status for the X forwarding dashboard."""
import json
from pathlib import Path
import re
import subprocess
import time

REPORT = Path('/var/lib/coinw-x-status/status.json')


def timer_status():
    try:
        result = subprocess.run(['systemctl', 'show', 'coinw-x-forward.timer',
                                 '--property=LoadState,ActiveState,UnitFileState,NextElapseUSecRealtime'],
                                capture_output=True, text=True, timeout=2)
        if result.returncode:
            return {'state': 'unknown', 'enabled': None, 'next_run': ''}
        values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if values.get('LoadState') == 'not-found':
            state = 'not_installed'
        else:
            state = values.get('ActiveState', 'unknown')
        return {'state': state if state in ('not_installed', 'active', 'inactive', 'failed', 'activating') else 'unknown',
                'enabled': values.get('UnitFileState') == 'enabled',
                'next_run': values.get('NextElapseUSecRealtime', '')[:100]}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {'state': 'unknown', 'enabled': None, 'next_run': ''}


def snapshot():
    now = time.time()
    result = {'available': False, 'server_time': now, 'timer': timer_status(), 'posts': []}
    try:
        with REPORT.open('rb') as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError()
        data = json.loads(raw)
        if data.get('version') != 1:
            raise ValueError()
        result.update(available=True, source='CoinWOfficial', channel='coinwofficialchannel', bot='CoinWCommunityHubBot', interval_seconds=600)
        for key in ('updated_at', 'enabled_at', 'last_check', 'last_success', 'last_delivery', 'next_fetch', 'next_send', 'last_error_at'):
            value = float(data.get(key, 0))
            if not 0 <= value < 1e12:
                raise ValueError()
            result[key] = value
        # Never expose arbitrary error text or unrecognized fields from the file.
        error = str(data.get('last_error', ''))
        result['last_error'] = error if re.fullmatch(r'[a-z_]*(?:_post_\d+)?', error) else 'unknown_error'
        for field, states in (('counts', ('baseline', 'pending', 'sent')), ('delivery_counts', ('sent', 'sending', 'uncertain', 'failed', 'retry'))):
            result[field] = {key: max(0, int(data.get(field, {}).get(key, 0))) for key in states}
        for post in data.get('posts', [])[:100]:
            pid = str(post['id'])
            if not re.fullmatch(r'\d{1,30}', pid):
                continue
            item = {'id': pid, 'url': 'https://x.com/CoinWOfficial/status/' + pid,
                    'published': float(post['published']), 'text': str(post['text'])[:1200],
                    'images': max(0, int(post['images'])),
                    'status': post['status'] if post['status'] in ('baseline', 'pending', 'sent') else 'unknown', 'deliveries': []}
            for delivery in post.get('deliveries', [])[:100]:
                state = delivery.get('status')
                mid = delivery.get('message_id')
                item['deliveries'].append({'part': int(delivery['part']),
                    'status': state if state in ('sending', 'sent', 'uncertain', 'failed', 'retry') else 'unknown',
                    'message_url': f'https://t.me/coinwofficialchannel/{mid}' if isinstance(mid, int) and mid > 0 else None})
            result['posts'].append(item)
        result['stale'] = now - result['updated_at'] > 1200
        return result
    except FileNotFoundError:
        return {'available': False, 'reason': 'not_configured', 'server_time': now, 'timer': result['timer'], 'posts': []}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return {'available': False, 'reason': 'report_unavailable', 'server_time': now, 'timer': result['timer'], 'posts': []}
