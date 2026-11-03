from datetime import datetime,timezone
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

spec=importlib.util.spec_from_file_location('free_x_feeds',Path(__file__).resolve().parents[1]/'deploy/check_x_feeds.py')
feeds=importlib.util.module_from_spec(spec);spec.loader.exec_module(feeds)
NOW=datetime(2026,11,3,12,tzinfo=timezone.utc)
URL='https://nitter.example/CoinWOfficial/rss'


def rss(account='CoinWOfficial',date='Tue, 03 Nov 2026 10:00:00 +0000',body='<![CDATA[<p>中文 / 日本語 / Welcome</p><img src="https://pbs.twimg.com/image.jpg">]]>'):
    return f'''<?xml version="1.0"?><rss version="2.0"><channel><title>CoinW</title><item>
    <link>https://nitter.example/{account}/status/1234567890123456789#m</link>
    <pubDate>{date}</pubDate><description>{body}</description></item></channel></rss>'''.encode()


class FeedTests(unittest.TestCase):
    def test_valid_recent_rss_keeps_correct_account_date_and_image(self):
        result=feeds.inspect_body(rss(), 'CoinWOfficial',URL,now=NOW)
        self.assertEqual(result['status'],'usable');self.assertEqual(result['recent_posts'],1)
        self.assertEqual(result['posts_with_images'],1)
        self.assertEqual(result['samples'][0]['url'],'https://x.com/CoinWOfficial/status/1234567890123456789')
        self.assertIn('日本語',result['samples'][0]['text_preview'])
    def test_atom_namespace_and_enclosure_image_are_supported(self):
        body=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://x.com/CoinWOfficial/status/1234</id><link href="https://x.com/CoinWOfficial/status/1234"/><updated>2026-11-03T11:00:00Z</updated><content type="html">Hello community!</content><link rel="enclosure" href="https://pbs.twimg.com/p.jpg" type="image/jpeg"/></entry></feed>'''
        result=feeds.inspect_body(body,'CoinWOfficial',URL,now=NOW)
        self.assertEqual(result['status'],'usable');self.assertEqual(result['posts'],1);self.assertEqual(result['posts_with_images'],1)
    def test_wrong_account_does_not_pass_just_because_feed_is_valid_xml(self):
        result=feeds.inspect_body(rss(account='OtherAccount'),'CoinWOfficial',URL,now=NOW)
        self.assertEqual(result['status'],'wrong_account');self.assertEqual(result['posts'],0)
    def test_old_or_future_dated_content_is_not_announced_as_current(self):
        for date in ('Tue, 03 Nov 2020 10:00:00 +0000','Tue, 03 Nov 2030 10:00:00 +0000'):
            self.assertEqual(feeds.inspect_body(rss(date=date),'CoinWOfficial',URL,now=NOW)['status'],'stale')
    def test_missing_dates_require_review_and_html_challenge_is_not_a_feed(self):
        self.assertEqual(feeds.inspect_body(rss(date=''),'CoinWOfficial',URL,now=NOW)['status'],'undated')
        self.assertEqual(feeds.inspect_body(b'<html>Sign in to view this profile</html>','CoinWOfficial',URL,now=NOW)['status'],'not_feed')
    def test_xml_entities_and_oversize_responses_are_rejected(self):
        entity=b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/private">]><rss><channel>&x;</channel></rss>'
        self.assertEqual(feeds.inspect_body(entity,'CoinWOfficial',URL,now=NOW)['status'],'unsafe_xml')
        self.assertEqual(feeds.inspect_body(b'x'*(feeds.MAX_BYTES+1),'CoinWOfficial',URL,now=NOW)['status'],'too_large')
    def test_public_embed_requires_real_post_objects_for_expected_account(self):
        data={'props':{'entries':[{'user':{'screen_name':'CoinWOfficial'},'id_str':'1234567890123456789','full_text':'CoinW community news 💜','created_at':'Tue Nov 03 10:00:00 +0000 2026','extended_entities':{'media':[{'type':'photo'}]}}]}}
        body=('<html><script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script></html>').encode()
        result=feeds.inspect_body(body,'CoinWOfficial',URL,'embed',NOW)
        self.assertEqual(result['status'],'usable');self.assertEqual(result['posts_with_images'],1)
        self.assertEqual(feeds.inspect_body(body,'OtherAccount',URL,'embed',NOW)['status'],'wrong_account')
    def test_proxy_denial_is_distinguished_from_actual_origin_http_403(self):
        source=('example',URL,'feed')
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('Tunnel connection failed: 403 Forbidden')):
            result=feeds.check(source,'CoinWOfficial');self.assertEqual(result['status'],'environment_blocked')
        with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError(URL,403,'Forbidden',{},io.BytesIO())):
            result=feeds.check(source,'CoinWOfficial');self.assertEqual(result['status'],'http_error');self.assertEqual(result['http'],403)
    def test_account_links_cannot_point_to_unrelated_hosts(self):
        self.assertIsNone(feeds.post_id('https://evil.example/CoinWOfficial/status/123','CoinWOfficial','nitter.example'))
        self.assertEqual(feeds.post_id('https://twitter.com/coinwofficial/status/123','CoinWOfficial','nitter.example'),'123')
    def test_text_preview_drops_script_contents_and_does_not_download_media(self):
        result=feeds.inspect_body(rss(body='<![CDATA[<script>private()</script><p>Hello community!</p>]]>'),'CoinWOfficial',URL,now=NOW)
        self.assertEqual(result['samples'][0]['text_preview'],'Hello community!')
    def test_empty_link_only_entries_are_not_mistaken_for_usable_content(self):
        result=feeds.inspect_body(rss(body=''),'CoinWOfficial',URL,now=NOW)
        self.assertNotEqual(result['status'],'usable')
    def test_relative_image_urls_are_detected_without_fetching_them(self):
        result=feeds.inspect_body(rss(body='<![CDATA[<p>Hello community</p><img src="/pic/image.jpg">]]>'),'CoinWOfficial',URL,now=NOW)
        self.assertEqual(result['posts_with_images'],1)


if __name__=='__main__':unittest.main()
