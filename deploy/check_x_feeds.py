#!/usr/bin/env python3
"""Read-only probe of public, no-key X RSS/embedded sources. Never contacts Telegram."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit,urljoin
import xml.etree.ElementTree as ET

MAX_BYTES=2_000_000
SOURCES=(
    ('RSSHub','https://rsshub.app/twitter/user/{account}','feed'),
    ('RSSHub mirror','https://rsshub.rssforever.com/twitter/user/{account}','feed'),
    ('Nitter','https://nitter.net/{account}/rss','feed'),
    ('Nitter Poast','https://nitter.poast.org/{account}/rss','feed'),
    ('Nitter Privacyredirect','https://nitter.privacyredirect.com/{account}/rss','feed'),
    ('X public embed','https://syndication.twitter.com/srv/timeline-profile/screen-name/{account}','embed'),
)
LABELS={
    'usable':'返回了对应账号的近期帖子，可继续验证实时性',
    'stale':'订阅格式有效，但没有7天内的已核实帖子',
    'undated':'有对应账号帖子，但缺少可验证的发布时间',
    'wrong_account':'没有发现对应账号的有效帖子内容',
    'not_feed':'返回网页/验证页面或空数据，未拿到可用帖子',
    'invalid_xml':'返回的XML无法解析',
    'too_large':'响应超过大小限制，未解析',
    'unsafe_xml':'含实体或DOCTYPE声明，未解析',
    'environment_blocked':'检测环境代理拦截，不能据此判断订阅源可用性',
    'http_error':'站点返回HTTP错误',
    'network_error':'网络或TLS连接失败，未验证',
}


class PlainHTML(HTMLParser):
    def __init__(self):super().__init__();self.text=[];self.images=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag in ('script','style'):self.hidden+=1
        if tag in ('br','p','div'):self.text.append('\n')
        if tag=='img' and values.get('src'):self.images.append(values['src'])
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.hidden:self.hidden-=1
    def handle_data(self,data):
        if not self.hidden:self.text.append(data)


def when(value):
    if not isinstance(value,str) or not value.strip():return None
    for parser in (parsedate_to_datetime,datetime.fromisoformat,lambda s:datetime.strptime(s,'%a %b %d %H:%M:%S %z %Y')):
        try:
            result=parser(value.strip())
            return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
        except (ValueError,TypeError,OverflowError):pass
    return None


def post_id(link,account,feed_host):
    try:
        parsed=urlsplit(link)
        if parsed.hostname not in ('x.com','www.x.com','twitter.com','www.twitter.com','mobile.twitter.com',feed_host):return None
        match=re.fullmatch(r'/([^/]+)/status/(\d+)/?',parsed.path)
        return match[2] if match and match[1].casefold()==account.casefold() else None
    except ValueError:return None


def parse_feed(body,account,feed_host):
    if len(body)>MAX_BYTES:return [],'too_large'
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():return [],'unsafe_xml'
    try:root=ET.fromstring(body)
    except ET.ParseError:return [],'invalid_xml' if body.lstrip().startswith((b'<?xml',b'<rss',b'<feed')) else 'not_feed'
    def local(tag):return tag.rsplit('}',1)[-1]
    if local(root.tag) not in ('rss','feed','RDF'):return [],'not_feed'
    posts=[]
    for entry in root.iter():
        if local(entry.tag) not in ('item','entry'):continue
        values={};links=[];images=[]
        for child in entry.iter():
            name=local(child.tag)
            if name=='link':
                if child.get('href') and child.get('rel','alternate')=='alternate':links.append(child.get('href'))
                elif child.text:links.append(child.text.strip())
                if child.get('rel')=='enclosure' and child.get('type','').startswith('image/') and child.get('href','').startswith('https://'):images.append(child.get('href'))
            if name in ('guid','id') and child.text:links.append(child.text.strip())
            if name in ('title','description','encoded','summary','content','pubDate','published','updated','date') and child.text:values[name]=child.text
            if name in ('enclosure','thumbnail') or (name=='content' and child.get('url')):
                url=child.get('url','')
                if url.startswith('https://') and (name=='thumbnail' or child.get('medium')=='image' or child.get('type','').startswith('image/') or 'twimg.com' in (urlsplit(url).hostname or '')):images.append(url)
        pid=next((pid for link in links if (pid:=post_id(link,account,feed_host))),None)
        if not pid:continue
        raw=values.get('encoded') or values.get('content') or values.get('description') or values.get('summary') or values.get('title','')
        parser=PlainHTML()
        try:parser.feed(raw)
        except Exception:pass
        date=next((parsed for key in ('pubDate','published','updated','date') if (parsed:=when(values.get(key)))),None)
        preview=re.sub(r'\s+',' ',''.join(parser.text)).strip()[:120]
        images+= [urljoin('https://'+feed_host+'/',src) for src in parser.images if urljoin('https://'+feed_host+'/',src).startswith('https://')]
        if not preview and not images:continue
        posts.append({'id':pid,'url':f'https://x.com/{account}/status/{pid}',
                      'published':date.isoformat() if date else None,
                      'text_preview':preview,'images':len(set(images))})
    return list({post['id']:post for post in posts}.values()),None


def parse_embed(body,account):
    if len(body)>MAX_BYTES:return [],'too_large'
    text=body.decode('utf-8','replace')
    script=re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',text,re.S|re.I)
    if not script:return [],'not_feed'
    try:data=json.loads(script[1])
    except (ValueError,RecursionError):return [],'not_feed'
    posts=[];pending=[data]
    while pending:
        value=pending.pop()
        if isinstance(value,list):pending.extend(value)
        elif isinstance(value,dict):
            pending.extend(value.values())
            user=value.get('user',{})
            if not isinstance(user,dict) or str(user.get('screen_name','')).casefold()!=account.casefold():continue
            pid=value.get('id_str') or value.get('id')
            raw=value.get('full_text') or value.get('text')
            if not str(pid).isdigit() or not isinstance(raw,str):continue
            date=when(value.get('created_at'))
            entities=value.get('extended_entities') or value.get('entities') or {}
            media=entities.get('media',[]) if isinstance(entities,dict) else []
            if not isinstance(media,list):media=[]
            posts.append({'id':str(pid),'url':f'https://x.com/{account}/status/{pid}','published':date.isoformat() if date else None,
                          'text_preview':re.sub(r'\s+',' ',raw)[:120],'images':sum(m.get('type')=='photo' for m in media if isinstance(m,dict))})
    return list({post['id']:post for post in posts}.values()),None


def inspect_body(body,account,url,kind='feed',now=None):
    posts,error=parse_embed(body,account) if kind=='embed' else parse_feed(body,account,urlsplit(url).hostname)
    if error:return {'status':error,'posts':0,'samples':[]}
    now=now or datetime.now(timezone.utc)
    recent=[p for p in posts if p['published'] and timedelta(minutes=-10)<=now-datetime.fromisoformat(p['published'])<=timedelta(days=7)]
    dated=[p for p in posts if p['published']]
    status='usable' if recent else 'stale' if dated else 'undated' if posts else 'wrong_account'
    posts.sort(key=lambda p:int(p['id']),reverse=True)
    return {'status':status,'posts':len(posts),'recent_posts':len(recent),'posts_with_images':sum(p['images']>0 for p in posts),'samples':posts[:3]}


def check(source,account,timeout=15):
    name,template,kind=source;url=template.format(account=account)
    report={'source':name,'url':url,'kind':kind}
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'CoinWFeedCheck/1.0','Accept':'application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.5'})
        with urllib.request.urlopen(request,timeout=timeout) as response:
            body=response.read(MAX_BYTES+1)
            report.update(http=response.status,content_type=response.headers.get('Content-Type',''))
            effective_url=response.geturl()
        report.update(inspect_body(body,account,effective_url,kind))
    except urllib.error.HTTPError as error:report.update(status='http_error',http=error.code)
    except urllib.error.URLError as error:
        reason=str(error.reason)
        report.update(status='environment_blocked' if 'Tunnel connection failed: 403' in reason else 'network_error',error_type=type(error.reason).__name__)
    except (OSError,ValueError) as error:report.update(status='network_error',error_type=type(error).__name__)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',default='CoinWOfficial');parser.add_argument('--timeout',type=int,default=15)
    parser.add_argument('--json',action='store_true',help='Output diagnostic JSON without reading any credentials')
    args=parser.parse_args()
    account=args.account.lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',account):parser.error('Invalid X account name')
    if not 5<=args.timeout<=30:parser.error('Timeout must be between 5 and 30 seconds')
    if not args.json:print(f'只读检测 @{account} 的免费公开订阅：不读取凭证，不调用Telegram。',flush=True)
    with ThreadPoolExecutor(max_workers=6) as executor:reports=list(executor.map(lambda source:check(source,account,args.timeout),SOURCES))
    if args.json:
        print(json.dumps({'account':account,'checked_at':datetime.now(timezone.utc).isoformat(),'results':reports},ensure_ascii=False,indent=2));return
    for result in reports:
        print('\n'+result['source']+'：'+LABELS[result['status']]+(f" [HTTP {result['http']}]" if result.get('http') else ''))
        print('  '+result['url'])
        if result.get('posts') is not None:print(f"  匹配帖子 {result['posts']}，7天内 {result.get('recent_posts',0)}，含图片 {result.get('posts_with_images',0)}")
        for post in result.get('samples',[])[:2]:print('  '+(post['published'] or '日期未知')+' · '+post['url'])
    usable=[r for r in reports if r['status']=='usable']
    print('\n验证结果：'+(f'有 {len(usable)} 个来源返回近期帖子。还需对照X原帖并观察下一条新帖是否及时更新，尚未自动转发。' if usable else '本次没有确认可用且近期的来源。网络拦截、需要登录、源失效或内容过旧需分别处理，不能据此保证免费自动同步。'))
    print('请分享以上检测结果；不要分享Bot Token、密码、Cookie或服务器配置文件。')


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:
        print('\n检测已停止。',file=sys.stderr);sys.exit(130)
