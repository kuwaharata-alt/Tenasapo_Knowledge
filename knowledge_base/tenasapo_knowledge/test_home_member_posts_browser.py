"""Real home markup/JS in Chrome; Chart is a recording boundary, no CDN needed."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class HomeMemberPostsBrowserTests(SimpleTestCase):
    def run_browser(self, checks, *, chart='recording', empty=False):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        unsafe = '</script><img id="post-xss" src=x onerror="window.xss=true">'
        periods = {}
        for period, faq, tips in (('all', 8, 3), ('current', 0, 0), ('previous', 2, 1)):
            periods[period] = {
                'rows': [] if empty else [dict(name=unsafe, faq_post_count=faq,
                                               tips_post_count=tips, post_count_total=faq + tips)],
                'totals': dict(faq_post_count=faq, tips_post_count=tips, post_count_total=faq + tips),
            }
        context = {'home_member_post_periods': periods, 'home_member_post_totals': periods['all']['totals']}
        markup = render_to_string('includes/home_member_posts.html', context)
        payload = Template('{{ home_member_post_periods|json_script:"home-member-post-data" }}').render(Context(context))
        assets = (Path(__file__).parent.parent / 'templates/includes/home_member_posts_assets.html').read_text(encoding='utf-8')
        recording = r'''<script>
window.instances = []; window.updates = 0;
window.Chart = class {
 constructor(canvas, config) {
  this.data = config.data; this.options = config.options; this.config = config;
  instances.push(this);
 }
 update() { window.updates++; }
 destroy() {}
};
</script>''' if chart == 'recording' else (
            '<script>window.Chart = class {constructor() {throw new Error("Unavailable canvas");}};</script>'
            if chart == 'broken' else ''
        )
        harness = r'''<script>
document.addEventListener('DOMContentLoaded', () => {
 const root = document.getElementById('home-member-posts');
 const button = period => root.querySelector(`[data-home-post-period="${period}"]`);
 const assert = (ok, message) => {if (!ok) throw new Error(message);};
 const select = period => {
  button(period).click();
  assert(button(period).getAttribute('aria-pressed') === 'true', 'selected period accessible');
  assert(root.querySelectorAll('[aria-pressed="true"]').length === 1, 'one selected period');
  const visible = [...root.querySelectorAll('[data-home-post-table]')].filter(node => !node.hidden);
  assert(visible.length === 1 && visible[0].dataset.homePostTable === period, 'matching table');
 };
 try {
  const originalURL = location.href;
  assert(!document.getElementById('post-xss') && !window.xss, 'safe member text');
  assert(root.querySelectorAll('canvas').length === 1, 'one canvas');
  CHECKS
  assert(location.href === originalURL, 'no navigation');
  assert(document.documentElement.scrollWidth <= innerWidth, 'no page horizontal overflow');
  document.getElementById('result').textContent = 'PASS';
 } catch (error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
});
</script>'''.replace('CHECKS', checks)
        with tempfile.TemporaryDirectory(prefix='nexus-home-chart-') as directory:
            page = Path(directory) / 'home.html'
            page.write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                            + markup + payload + recording + assets + assets
                            + '<pre id="result">PENDING</pre>' + harness, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--window-size=390,800', '--virtual-time-budget=2000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            outcome = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertEqual(outcome, '<pre id="result">PASS', outcome or result.stderr[-2000:])

    def test_period_switch_updates_one_stacked_chart_totals_and_accessible_table(self):
        self.run_browser(r'''
assert(instances.length === 1, 'duplicate assets must not initialize twice');
const chart = instances[0];
assert(chart.config.type === 'bar', 'bar chart');
assert(chart.options.scales.x.stacked && chart.options.scales.y.stacked, 'stacked axes');
assert(chart.data.datasets.map(item => item.label).join(',') === 'Q&A,Tips', 'legend order');
assert(root.querySelector('thead').textContent.includes('Q&A'), 'table label decoded once');
assert(root.querySelector('canvas').getAttribute('aria-label').includes('Q&A'), 'chart accessible label decoded once');
assert(chart.data.datasets[0].backgroundColor.includes('232, 117, 36'), 'orange FAQ');
assert(chart.data.datasets[1].backgroundColor.includes('73, 136, 232'), 'blue Tips');
assert(!root.querySelector('[data-home-post-chart]').hidden, 'chart displayed');
assert(!root.querySelector('details').open, 'table remains available via disclosure');
select('current');
assert(chart.data.datasets[0].data[0] === 0 && chart.options.scales.y.suggestedMax === 5, 'zero period scale');
assert(root.querySelector('[role="status"]').textContent.includes('当月：合計 0 件'), 'current status');
select('previous');
assert(chart.data.datasets[0].data[0] === 2 && chart.data.datasets[1].data[0] === 1, 'previous values');
select('all');
assert(chart.data.datasets[0].data[0] === 8 && updates === 3 && instances.length === 1, 'reuse instance');
const drawn = [];
chart.ctx = {save() {}, restore() {}, fillText(value) {drawn.push(value);}};
chart.getDatasetMeta = () => ({data: [{x: 10, y: 20}]});
chart.config.plugins[0].afterDatasetsDraw(chart);
assert(drawn.join() === '11', 'total above stacked bar');
''')

    def test_missing_or_failed_chart_keeps_readable_switchable_tables(self):
        for chart in ('missing', 'broken'):
            with self.subTest(chart=chart):
                self.run_browser(r'''
select('current'); select('previous'); select('all');
assert(root.querySelector('details').open, 'fallback table open');
assert(root.querySelector('[data-home-post-chart]').hidden, 'unavailable chart hidden');
assert(root.querySelector('tbody').textContent.includes('\x3c/script>\x3cimg'), 'literal member name');
''', chart=chart)

    def test_empty_registry_does_not_initialize_chart(self):
        self.run_browser(r'''
select('previous'); select('current'); select('all');
assert(instances.length === 0, 'no empty chart');
assert(root.textContent.includes('集計対象のメンバーはいません。'), 'empty state');
assert(root.querySelector('details').open, 'empty table readable');
''', empty=True)