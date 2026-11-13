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
                'target_chart': {'value': 20 if period == 'all' else 0 if period == 'current' else 4, 'label': '投稿目標'},
            }
        context = {'home_member_post_periods': periods, 'home_member_post_totals': periods['all']['totals']}
        markup = render_to_string('includes/home_member_posts.html', context)
        payload = Template('{{ home_member_post_periods|json_script:"home-member-post-data" }}').render(Context(context))
        assets = (Path(__file__).parent.parent / 'templates/includes/home_member_posts_assets.html').read_text(encoding='utf-8')
        target_assets = render_to_string('tenasapo_knowledge/includes/summary_target_chart.html')
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
    assert(root.querySelectorAll('[data-home-post-period][aria-pressed="true"]').length === 1, 'one selected period');
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
                            + markup + payload + recording + target_assets + assets + assets
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
assert(chart.data.datasets[0].backgroundColor.includes('220, 53, 69'), 'red FAQ matches Analyze');
assert(chart.data.datasets[1].backgroundColor.includes('73, 136, 232'), 'blue Tips');
assert(root.querySelector('.home-post-legend').classList.contains('justify-content-end'), 'legend matches Analyze right alignment');
assert(root.querySelector('#home-member-posts-title .bi-bar-chart-fill'), 'Analyze heading icon');
assert(root.querySelector('#home-post-status').classList.contains('visually-hidden'), 'no extra visible count paragraph');
assert(root.querySelector('#home-post-target').classList.contains('visually-hidden'), 'target label appears on chart only');
assert(root.querySelector('[data-home-post-sort="post_count"]'), 'Analyze sort controls available');
assert(root.querySelector('details').hidden, 'table link not shown below chart');
assert(!root.querySelector('[data-home-post-chart]').hidden, 'chart displayed');
assert(!root.querySelector('details').open, 'table remains available via disclosure');
assert(chart.options.scales.y.suggestedMax === 26, 'target included in chart scale');
assert(chart.config.plugins[1].id === 'postingTargetLine', 'shared Analyze target plugin');
assert(root.querySelector('#home-post-target').textContent.includes('20 件'), 'all period target');
select('current');
assert(chart.data.datasets[0].data[0] === 0 && chart.options.scales.y.suggestedMax === 5, 'zero period scale');
assert(root.querySelector('[role="status"]').textContent.includes('当月：合計 0 件'), 'current status');
select('previous');
assert(root.querySelector('#home-post-target').textContent.includes('4 件'), 'target switches with period');
assert(chart.data.datasets[0].data[0] === 2 && chart.data.datasets[1].data[0] === 1, 'previous values');
select('all');
assert(chart.data.datasets[0].data[0] === 8 && updates === 3 && instances.length === 1, 'reuse instance');
const drawn = [];
chart.ctx = {save() {}, restore() {}, fillText(value) {drawn.push(value);}};
chart.getDatasetMeta = () => ({data: [{x: 10, y: 20}]});
chart.config.plugins[0].afterDatasetsDraw(chart);
assert(drawn.join() === '11', 'total above stacked bar');
root.querySelector('[data-home-post-sort="post_count"]').click();
assert(instances.length === 1 && updates === 4, 'sorting updates same chart');
assert(root.querySelector('[data-home-post-sort="post_count"]').getAttribute('aria-pressed') === 'true', 'sort selection retained');
''')

    def test_legend_toggles_datasets_and_visible_totals_across_periods_and_sorting(self):
        self.run_browser(r'''
const chart = instances[0];
const faq = root.querySelector('[data-home-post-dataset="0"]');
const tips = root.querySelector('[data-home-post-dataset="1"]');
const drawn = [];
chart.ctx = {save() {}, restore() {}, fillText(value) {drawn.push(value);}};
chart.getDatasetMeta = index => ({data: [{x: 10, y: index === 0 ? 30 : 20}]});
const total = () => {
 drawn.length = 0;
 chart.config.plugins[0].afterDatasetsDraw(chart);
 return drawn.join();
};
assert(faq.tagName === 'BUTTON' && tips.tagName === 'BUTTON', 'keyboard-accessible legends');
assert(total() === '11', 'both visible total');
faq.click();
assert(chart.data.datasets[0].hidden && !chart.data.datasets[1].hidden, 'hide FAQ only');
assert(faq.getAttribute('aria-pressed') === 'false', 'hidden FAQ accessible state');
assert(total() === '3', 'total excludes hidden FAQ');
select('previous');
assert(chart.data.datasets[0].hidden && total() === '1', 'period preserves hidden FAQ');
root.querySelector('[data-home-post-sort="post_count"]').click();
assert(chart.data.datasets[0].hidden, 'sorting preserves visibility');
tips.click();
assert(chart.data.datasets.every(dataset => dataset.hidden), 'both can be hidden');
assert(total() === '', 'no total label when both hidden');
faq.click();
assert(!chart.data.datasets[0].hidden && chart.data.datasets[1].hidden, 'restore FAQ only');
assert(total() === '2', 'FAQ-only total');
tips.click();
assert(chart.data.datasets.every(dataset => !dataset.hidden), 'restore both');
assert(total() === '3' && instances.length === 1, 'same chart restored');
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