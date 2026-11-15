"""Real view/template regressions and offline Chrome tests for detail tabs.

Use --settings=test_knowledge_tabs_settings to avoid dotenv and on-disk DBs.
The browser loads only temporary file:// pages, with networking blocked by CSP
and host resolver rules. No application server or browser driver is required.
"""

from datetime import timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import Http404
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.html import escape

from .models import KnowledgeArticle, TipsArticle


SPECS = ((KnowledgeArticle, 'article', 'article', 'Q&A一覧に戻る'),
         (TipsArticle, 'tip', 'tip', 'Tips一覧に戻る'))
UNSAFE_TITLE = '日本語 " onfocus="evil() <img src=x onerror=evil()> & \' </script>'
BODY = 'PRIVATE_BODY_TAB_REGRESSION'


class Links(HTMLParser):
    """Inspect parsed attributes, not just source substrings."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.tags = []
        self.current = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'a':
            self.current = {**attrs, 'text': ''}
            self.anchors.append(self.current)

    def handle_endtag(self, tag):
        if tag == 'a':
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data


class NoNetwork:
    def setUp(self):
        super().setUp()
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'urllib.request.urlopen', 'requests.sessions.Session.request'):
            guard = self.enterContext(patch(target, side_effect=AssertionError('live network forbidden')))
            self.addCleanup(guard.assert_not_called)


class OfflineChrome:
    def run_offline_page(self, source, *, expected, width=1280, javascript_enabled=True):
        candidates = [shutil.which('google-chrome'), shutil.which('chromium'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe']
        browser = next((p for p in candidates if p and Path(p).is_file()), None)
        if browser is None:
            self.skipTest('Chrome/Chromium is required for offline tab tests')
        with tempfile.TemporaryDirectory(prefix='knowledge-tabs-browser-') as directory:
            page = Path(directory) / 'fixture.html'
            page.write_text(source, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox',
                '--no-first-run', '--disable-background-networking', '--disable-sync',
                '--host-resolver-rules=MAP * ~NOTFOUND', '--no-proxy-server',
                f'--window-size={width},1000',
                f'--user-data-dir={directory}/profile', '--virtual-time-budget=2000',
                *([] if javascript_enabled else ['--blink-settings=scriptEnabled=false']),
                '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            report = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
            self.assertIsNotNone(report, result.stderr[-2000:])
            self.assertEqual(report.group(1), expected, report.group(1))
            return result.stdout


class KnowledgeTabViewTests(NoNetwork, OfflineChrome, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='offline-tabs-admin', email='admin@example.test', password='test',
        )
        cls.customer = get_user_model().objects.create_user(username='offline-tabs-customer')
        cls.customer.groups.add(Group.objects.get_or_create(name='カスタマー')[0])
        cls.demo = get_user_model().objects.create_user(username='offline-tabs-demo')
        cls.demo.groups.add(Group.objects.get_or_create(name='demo')[0])
        cls.items = {}
        cls.related = {}
        for model, prefix, _, _ in SPECS:
            cls.items[prefix] = model.objects.create(
                title=UNSAFE_TITLE, body=BODY, tags='tabs,regression',
                created_by=cls.admin, expires_on=None, visible_to_demo=True,
            )
            cls.related[prefix] = model.objects.create(
                title='関連 ' + UNSAFE_TITLE, body='RELATED_BODY', tags='tabs,regression',
                created_by=cls.admin, expires_on=None, visible_to_demo=True,
            )

    def page(self, prefix, *, detail=True, query=None, user=None, item=None):
        item = item or self.items[prefix]
        url = reverse(prefix + ('_detail' if detail else '_list'),
                      args=[item.management_code] if detail else None)
        request = RequestFactory().get(url, query or {})
        request.user = user or self.admin
        request.session = {}
        match = resolve(url)
        response = match.func(request, *match.args, **match.kwargs)
        response.render()
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode()

    def back(self, source):
        matches = [a for a in Links(source).anchors if a.get('id') == 'knowledge-detail-back']
        self.assertEqual(len(matches), 1)
        return matches[0]

    def assert_list_tab_layout(self, prefix):
        # Render the actual view, list template AND inherited base CSS. Only
        # missing CDN Bootstrap utilities are supplied locally, before real CSS.
        # Never supply any knowledge-detail-tab, icon or toggle sizing rules.
        scaffolding = '''<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';
connect-src 'none'; font-src 'none'; img-src 'none'; frame-src 'none'">
<style id="offline-bootstrap-utilities">
* { box-sizing: border-box; }
html { font-size: 16px; }
h3 { font-size: 1.75rem; margin: 0; }
.d-flex { display: flex !important; }
.flex-column { flex-direction: column !important; }
.flex-wrap { flex-wrap: wrap !important; }
.align-items-start { align-items: flex-start !important; }
.align-items-center { align-items: center !important; }
.align-items-end { align-items: flex-end !important; }
.flex-grow-1 { flex-grow: 1 !important; }
.flex-shrink-0 { flex-shrink: 0 !important; }
.gap-1 { gap: .25rem !important; }
.gap-2 { gap: .5rem !important; }
.gap-3 { gap: 1rem !important; }
.mb-1 { margin-bottom: .25rem !important; }
.mt-1 { margin-top: .25rem !important; }
.p-3 { padding: 1rem !important; }
.visually-hidden { position: absolute !important; width: 1px; height: 1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
.collapse:not(.show) { display: none; }
</style>'''
        checks = r'''<pre id="result">PENDING</pre><script>
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const near = (a, b, message) => assert(Math.abs(a - b) < .6,
    message + ': ' + a + ' != ' + b);
try {
    const tabs = [...document.querySelectorAll('h3.accordion-header .knowledge-detail-tab')];
    assert(tabs.length === EXPECTED_COUNT, 'all rendered article links are measured');
    assert(document.querySelectorAll('link, script[src], iframe').length === 0,
        'external resources removed');
    for (const rootSize of [16, 20]) {
        document.documentElement.style.fontSize = rootSize + 'px';
        for (const headingSize of [1.75, 3]) {
            for (const tab of tabs) {
                const heading = tab.closest('h3');
                heading.style.fontSize = headingSize + 'rem';
                const toggle = heading.querySelector('.faq-toggle');
                const icon = tab.querySelector('.bi-box-arrow-up-right');
                assert(toggle && icon, 'actual expand V and new-tab icon exist');
                const t = tab.getBoundingClientRect(), v = toggle.getBoundingClientRect();
                const css = getComputedStyle(tab), iconCss = getComputedStyle(icon);
                near(t.width, rootSize * 2, 'tab width 2rem');
                near(t.height, rootSize * 1.5, 'tab height 1.5rem');
                near(parseFloat(css.fontSize), rootSize * .8, 'tab font .8rem');
                near(parseFloat(iconCss.fontSize), rootSize * .8, 'icon font .8rem');
                assert(parseFloat(getComputedStyle(heading).fontSize) > parseFloat(iconCss.fontSize),
                    'icon does not inherit large h3 title font');
                for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
                    assert(parseFloat(css['border' + side + 'Width']) === 0, 'tab border 0: ' + side);
                }
                assert(t.width > 0 && v.height > 0, 'both controls have real layout');
                assert(t.bottom <= v.top + .6, 'new-tab is in upper row above expand V');
                near(t.x + t.width / 2, v.x + v.width / 2, 'tab and expand V center X match');
                const badge = tab.parentElement.querySelector('.knowledge-status');
                assert(Boolean(badge) === EXPECT_BADGE, 'status badge matches actual permissions');
                if (badge) {
                    const b = badge.getBoundingClientRect();
                    assert(b.width > 0 && b.right <= t.left + .6, 'tab is beside badge on its right');
                    near(b.y + b.height / 2, t.y + t.height / 2, 'badge and tab share upper row');
                }
            }
        }
    }
    document.getElementById('result').textContent = 'PASS: offline list tab layout';
} catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
}
</script>'''
        for user in (self.admin, self.customer, self.demo):
            with self.assertTemplateUsed('tenasapo_knowledge/' + (
                    'article_list.html' if prefix == 'article' else 'tips_list.html')):
                with self.assertTemplateUsed('base.html'):
                    _, rendered = self.page(prefix, detail=False, user=user)
            # Remove application JS and resource hints BEFORE Chrome parses the
            # document. The first head element's CSP blocks every network type.
            source = re.sub(r'<script\b[^>]*>.*?</script\s*>', '', rendered, flags=re.S | re.I)
            source = re.sub(r'<link\b[^>]*>', '', source, flags=re.I)
            source = source.replace('<head>', '<head>' + scaffolding, 1)
            expected_count = sum(prefix in items for items in (self.items, self.related))
            harness = checks.replace('EXPECTED_COUNT', str(expected_count))
            harness = harness.replace('EXPECT_BADGE', json.dumps(user == self.admin))
            source = source.replace('</body>', harness + '</body>', 1)
            for width in (390, 1280):
                with self.subTest(kind=prefix, user=user.username, width=width):
                    self.run_offline_page(source, expected='PASS: offline list tab layout', width=width)

    def test_qa_list_actual_browser_tab_layout(self):
        self.assert_list_tab_layout('article')

    def test_tips_list_actual_browser_tab_layout(self):
        self.assert_list_tab_layout('tip')

    def test_detail_back_shared_include_and_exact_flag_for_both_kinds(self):
        for _, prefix, _, label in SPECS:
            for flag in (None, '', '0', 'true', '01', '1 ', '1<script>evil()</script>', '1'):
                with self.subTest(kind=prefix, flag=flag):
                    with self.assertTemplateUsed('includes/knowledge_detail_back.html'):
                        _, source = self.page(prefix, query={} if flag is None else {'close_tab': flag})
                    back = self.back(source)
                    self.assertEqual(back['href'], reverse(prefix + '_list'))
                    self.assertEqual(back['text'], '← ' + ('戻る（タブを閉じる）' if flag == '1' else label))
                    self.assertEqual('aria-label' in back, flag == '1')
                    self.assertEqual('window.close()' in source, flag == '1')
                    if flag == '1':
                        self.assertEqual(back['aria-label'], '戻る（このタブを閉じる）')

    def test_list_new_tab_link_and_existing_related_link_for_both_kinds(self):
        for _, prefix, _, _ in SPECS:
            for user in (self.admin, self.customer, self.demo):
                for flag in (None, '1'):
                    with self.subTest(kind=prefix, user=user.username, flag=flag):
                        _, source = self.page(prefix, detail=False, user=user,
                                              query={} if flag is None else {'close_tab': flag})
                        links = Links(source).anchors
                        item = self.items[prefix]
                        href = reverse(prefix + '_detail', args=[item.management_code])
                        new_links = [a for a in links if a.get('title') == '別タブで開く'
                                     and a['href'] == href + '?close_tab=1']
                        self.assertEqual(len(new_links), 1)
                        link = new_links[0]
                        self.assertIn('knowledge-detail-tab', link['class'].split())
                        self.assertEqual(link['target'], '_blank')
                        self.assertIn('noopener', link['rel'].split())
                        self.assertEqual(link['aria-label'], UNSAFE_TITLE + 'を別タブで開く')
                        self.assertFalse(any(key.startswith('on') for key in link))
                        self.assertIn(str(escape(UNSAFE_TITLE)) + 'を別タブで開く"', source)
                        related_href = reverse(prefix + '_detail', args=[self.related[prefix].management_code])
                        ordinary_related = [a for a in links if a.get('href') == related_href]
                        self.assertEqual(bool(ordinary_related), user == self.admin)
                        self.assertTrue(all('target' not in a for a in ordinary_related))

    def test_detail_related_links_inherit_only_exact_flag_without_new_target(self):
        for _, prefix, _, _ in SPECS:
            for flag in (None, '0', 'true', '1'):
                with self.subTest(kind=prefix, flag=flag):
                    _, source = self.page(prefix, query={} if flag is None else {'close_tab': flag})
                    href = reverse(prefix + '_detail', args=[self.related[prefix].management_code])
                    related = [a for a in Links(source).anchors if a.get('href', '').startswith(href)]
                    self.assertEqual(len(related), 1)
                    self.assertEqual(related[0]['href'], href + ('?close_tab=1' if flag == '1' else ''))
                    self.assertNotIn('target', related[0])
                    self.assertIn('関連 ' + UNSAFE_TITLE, related[0]['text'])

    def test_escaped_titles_and_safe_analysis_return_destination(self):
        for _, prefix, _, _ in SPECS:
            safe_next = reverse('summary') + '?q="<img src=x onerror=evil()>&kind=' + prefix
            for flag in ('0', '1'):
                with self.subTest(kind=prefix, flag=flag):
                    _, source = self.page(prefix, query={'next': safe_next, 'close_tab': flag})
                    self.assertEqual(self.back(source)['href'], safe_next)
                    self.assertIn(str(escape(safe_next)), source)
                    self.assertIn(str(escape(UNSAFE_TITLE)), source)
                    # The pre-existing FAQ rich-text question fallback allows
                    # sanitized IMG tags. Check executable attributes rather
                    # than incorrectly forbidding every image in the page.
                    self.assertFalse(any(key.startswith('on') and 'evil()' in value
                                         for _, attrs in Links(source).tags
                                         for key, value in attrs.items() if value))
                    if flag == '0':
                        self.assertEqual(self.back(source)['text'], '← アナライズに戻る')
            for unsafe in ('https://evil.example/summary/', '//evil.example/summary/',
                           'javascript:alert(1)', '/faq/', '/summary/../other/',
                           'https://[invalid', '/\\evil.example/summary/'):
                with self.subTest(kind=prefix, unsafe=unsafe):
                    _, source = self.page(prefix, query={'next': unsafe, 'close_tab': '1'})
                    self.assertEqual(self.back(source)['href'], reverse(prefix + '_list'))

    def test_flag_does_not_change_content_edit_or_related_permissions(self):
        for _, prefix, key, _ in SPECS:
            for user in (self.admin, self.customer, self.demo):
                for detail in (False, True):
                    for flag in ('0', '1'):
                        with self.subTest(kind=prefix, user=user.username, detail=detail, flag=flag):
                            response, source = self.page(prefix, user=user, detail=detail, query={'close_tab': flag})
                            self.assertEqual(response.context_data['can_edit_' + key], user == self.admin)
                            self.assertEqual(BODY in source, user != self.demo)
                            edit_url = reverse(prefix + '_edit', args=[self.items[prefix].pk])
                            self.assertEqual(any(a.get('href', '').split('?')[0] == edit_url
                                                 for a in Links(source).anchors), user == self.admin)
                            if detail:
                                self.assertEqual(response.context_data[key].can_view_content, user != self.demo)
                                related_url = reverse(prefix + '_detail', args=[self.related[prefix].management_code])
                                self.assertEqual(any(a.get('href', '').split('?')[0] == related_url
                                                     for a in Links(source).anchors), user == self.admin)

    def test_flag_does_not_bypass_list_or_detail_visibility_approval_or_expiration(self):
        for model, prefix, _, _ in SPECS:
            for restriction in ({'is_published': False}, {'visible_to_customer': False},
                                {'is_approved': False},
                                {'expires_on': timezone.localdate() - timedelta(days=1)}):
                item = model.objects.create(title='not available', body=BODY, **restriction)
                for flag in ('0', '1'):
                    with self.subTest(kind=prefix, restriction=restriction, flag=flag):
                        with self.assertRaises(Http404):
                            self.page(prefix, item=item, user=self.customer, query={'close_tab': flag})
                        _, source = self.page(prefix, detail=False, user=self.customer,
                                              query={'close_tab': flag})
                        href = reverse(prefix + '_detail', args=[item.management_code])
                        self.assertFalse(any(a.get('href', '').split('?')[0] == href
                                             for a in Links(source).anchors))

    def test_shared_include_escapes_back_url_and_normal_label_as_html_not_javascript(self):
        href = '/faq/?q=" onfocus="evil()&x=<script>evil()</script>'
        for flag in ('0', '1'):
            with self.subTest(flag=flag):
                source = render_to_string('includes/knowledge_detail_back.html', {
                    'request': RequestFactory().get('/detail/', {'close_tab': flag}),
                    'detail_back_url': href, 'detail_back_label': UNSAFE_TITLE,
                })
                back = self.back(source)
                self.assertEqual(back['href'], href)
                self.assertFalse(any(key.startswith('on') for key in back))
                self.assertIn(str(escape(href)), source)
                if flag == '0':
                    self.assertEqual(back['text'], '← ' + UNSAFE_TITLE)
                else:
                    script = re.search(r'<script>(.*?)</script>', source, re.S).group(1)
                    self.assertNotIn(href, script)
                    self.assertNotIn('evil()', script)


class KnowledgeTabBrowserTests(NoNetwork, OfflineChrome, SimpleTestCase):
    def run_browser(self, checks, *, flag='1', mode='refused'):
        request = RequestFactory().get('/detail/', {'close_tab': flag})
        rendered = render_to_string('includes/knowledge_detail_back.html', {
            'request': request, 'detail_back_url': '#fallback', 'detail_back_label': '一覧に戻る',
        })
        setup = r'''<script>
const nativeTimeout = window.setTimeout.bind(window);
const calls = [], timers = [];
let closedState = false;
Object.defineProperty(window, 'closed', {configurable: true, get: () => closedState});
window.close = () => {
    calls.push(performance.now());
    if (MODE === 'throws') throw new Error('close forbidden');
    if (MODE === 'success') closedState = true;
};
window.setTimeout = (callback, delay, ...args) => {
    timers.push(delay);
    return nativeTimeout(callback, delay, ...args);
};
</script>'''.replace('MODE', json.dumps(mode))
        harness = r'''<pre id="result">PENDING</pre><script>
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const sleep = ms => new Promise(resolve => nativeTimeout(resolve, ms));
const back = document.getElementById('knowledge-detail-back');
const click = options => {
    const event = new MouseEvent('click', {bubbles: true, cancelable: true, button: 0, ...options});
    let prevented;
    // Observe after the production listener, then suppress the native default
    // so modifier tests cannot open real tabs or navigate the fixture.
    const observe = e => { prevented = e.defaultPrevented; e.preventDefault(); };
    document.addEventListener('click', observe, {once: true});
    back.dispatchEvent(event);
    return prevented;
};
(async () => {
    try {
        CHECKS
        document.getElementById('result').textContent = 'PASS: offline detail tabs';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
    }
})();
</script>'''.replace('CHECKS', checks)
        self.run_offline_page(
            '<!doctype html><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'script-src \'unsafe-inline\'; connect-src \'none\'">'
            + setup + rendered + harness, expected='PASS: offline detail tabs',
        )

    def test_close_refused_or_throws_falls_back_to_actual_href_after_150ms(self):
        for mode in ('refused', 'throws'):
            with self.subTest(mode=mode):
                self.run_browser(r'''
                    const started = performance.now();
                    assert(click() === true, 'ordinary click intercepted');
                    assert(calls.length === 1, 'window.close called exactly once');
                    assert(timers.length === 1 && timers[0] === 150, 'fallback timer is exactly 150ms');
                    assert(location.hash === '', 'no synchronous navigation');
                    await sleep(100);
                    assert(location.hash === '', 'no fallback before timer');
                    await sleep(120);
                    assert(location.hash === '#fallback', 'actual location.assign uses back.href');
                    assert(performance.now() - started >= 150, 'fallback waits 150ms');
                ''', mode=mode)

    def test_successful_close_does_not_fall_back(self):
        self.run_browser(r'''
            assert(click() === true && calls.length === 1, 'ordinary click closes');
            await sleep(220);
            assert(window.closed && location.hash === '', 'closed tab never navigates');
            assert(timers.length === 1 && timers[0] === 150, 'one fallback check');
        ''', mode='success')

    def test_all_modifiers_and_non_left_buttons_keep_native_behavior(self):
        self.run_browser(r'''
            for (const options of [{ctrlKey:true}, {metaKey:true}, {shiftKey:true},
                                   {altKey:true}, {button:1}, {button:2},
                                   {ctrlKey:true, shiftKey:true}]) {
                assert(click(options) === false, 'modified/non-left click not intercepted: ' + JSON.stringify(options));
            }
            await sleep(220);
            assert(calls.length === 0 && timers.length === 0, 'no close or fallback for excluded clicks');
            assert(click() === true && calls.length === 1, 'later ordinary click still works');
            await sleep(220);
            assert(location.hash === '#fallback', 'ordinary click retains fallback');
        ''')

    def test_normal_detail_click_has_no_close_handler_and_retains_native_navigation(self):
        self.run_browser(r'''
            assert(click() === false, 'ordinary normal-page click not intercepted');
            assert(click({ctrlKey:true}) === false, 'modified normal-page click not intercepted');
            assert(calls.length === 0 && timers.length === 0, 'no close script on normal page');
            back.click();
            await sleep(220);
            assert(location.hash === '#fallback', 'native href navigation retained');
            assert(calls.length === 0 && timers.length === 0, 'native navigation never closes');
        ''', flag='0')