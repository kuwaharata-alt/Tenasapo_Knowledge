"""Run the actual summary JavaScript in Chrome; mock only the Chart boundary."""

import os
from datetime import date
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.template import Context, Template
from django.test import SimpleTestCase


class SummaryTargetChartBrowserTests(SimpleTestCase):
    def run_browser(self, target, *, actual=3, empty=False, width=1440):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        source = (Path(__file__).parent / 'templates/tenasapo_knowledge/summary.html').read_text(encoding='utf-8')
        header = source.split('{% block page_header %}', 1)[1].split('{% endblock %}', 1)[0]
        self.assertIn('id="summaryTabs"', header)
        self.assertEqual(header.count('data-bs-toggle="tab"'), 3)
        styles = source.split('{% block extra_css %}', 1)[1].split('{% endblock %}', 1)[0].replace('{{ block.super }}', '')
        source = source.split('{% block content %}', 1)[1].split('{% endblock %}', 1)[0]
        self.assertNotIn('id="summaryTabs"', source)
        source = source.replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>', '')
        unsafe = '</script><img id="target-xss" src=x onerror="window.xss=true">'
        stats = {
            'post_count_total': 12345, 'approved_count_total': 123,
            'faq_post_count': 12300, 'faq_approved_count': 120,
            'tips_post_count': 45, 'tips_approved_count': 3,
            'monthly_totals': [{'month': '2026-05', 'post_count_total': 1},
                               {'month': '2026-11', 'post_count_total': 12345,
                                'approved_count_total': 123}],
        }
        markup = Template(source).render(Context({
            'member_summaries': [] if empty else [{'name': unsafe, **stats}],
            'summary_totals': stats,
            'posting_targets': {'target_month': date(2026, 11, 1)},
            'chart_labels': [] if empty else [unsafe],
            'chart_faq_post_counts': [] if empty else [actual],
            'chart_tips_post_counts': [] if empty else [0],
            'posting_target_chart': {'value': target, 'label': '1人あたり Q&A＋Tips目標'},
            'internal_chart_labels': [], 'internal_post_chart_data': [],
            'internal_review_chart_data': [], 'internal_like_chart_data': [],
        }))
        recording = r'''<script>
window.instances = [];
sessionStorage.setItem('summary-navigation-scroll', JSON.stringify({url: location.href, x: 0, y: 180, savedAt: Date.now()}));
window.Chart = class {
 constructor(canvas, config) {
  this.config = config; this.data = config.data; this.options = config.options;
  this.visible = [true, true]; this.updates = 0; instances.push(this);
 }
 isDatasetVisible(index) {return this.visible[index];}
 setDatasetVisibility(index, visible) {this.visible[index] = visible;}
 getDatasetMeta(index) {return {data: [{x: 10, y: index === 0 ? 30 : 20}]};}
 update() {this.updates++;}
};
</script>'''
        checks = r'''<script>
window.addEventListener('load', () => setTimeout(() => {
 const assert = (ok, message) => {if (!ok) throw new Error(message);};
 try {
    const main = document.getElementById('app-main');
    assert(main.scrollTop === 180 && window.scrollY === 0, 'navigation restores actual app-main scroll rather than window position');
    assert(!sessionStorage.getItem('summary-navigation-scroll'), 'restoration consumes saved position');
    const help = document.getElementById('posting-target-help');
    const helpToggle = document.querySelector('[data-posting-help-toggle]');
    assert(!help.open, 'explanations are collapsed initially');
    assert(helpToggle.getAttribute('aria-label') === '投稿目標の説明を開閉', 'icon has an accessible name');
    helpToggle.click();
    assert(help.open, 'click opens explanations');
    assert(helpToggle.getAttribute('aria-expanded') === 'true', 'expanded state updated');
    helpToggle.click();
    assert(!help.open, 'second click closes explanations');
    assert(helpToggle.getAttribute('aria-expanded') === 'false', 'collapsed state updated');
    assert(helpToggle.parentElement.contains(document.getElementById('posting-target-title')), 'info button is next to heading');
    const card = document.querySelector('.posting-target-card');
    document.querySelectorAll('h2').forEach(heading => {
     assert(heading.classList.contains('summary-section-title'), 'all section headings use the shared style');
     assert(heading.querySelector('i.bi[aria-hidden="true"]'), 'all section headings have decorative icons');
    });
    assert(Array.from(card.querySelectorAll('a')).some(link => link.textContent.includes('投稿目標設定')), 'settings belong inside target card');
    if (document.getElementById('summary-member-table')) {
     const total = document.getElementById('summary-total-table');
     const members = document.getElementById('summary-member-table');
    const controls = document.getElementById('member-chart-controls');
    const legend = document.getElementById('member-chart-legend');
    const chartArea = document.getElementById('member-chart-area');
    const sortButton = document.getElementById('member-chart-sort');
    assert(sortButton && sortButton.parentElement.parentElement.querySelector('h2'), 'sort icon belongs beside chart heading');
    const sortLinks = sortButton.parentElement.querySelectorAll('a');
    assert(sortLinks.length === 2 && new URL(sortLinks[1].href).searchParams.get('sort_by') === 'post_count', 'sort menu has both existing choices');
    [...controls.querySelectorAll('a'), ...sortLinks].forEach(link => {
        main.scrollTop = 260;
     link.addEventListener('click', event => event.preventDefault(), {once: true});
     link.click();
     const saved = JSON.parse(sessionStorage.getItem('summary-navigation-scroll'));
    assert(saved.url === link.href && saved.y === 260 && window.scrollY === 0, 'period and sort links save actual main scroll position for destination');
    });
    assert(chartArea.contains(legend) && !controls.contains(legend), 'legend belongs directly with the chart rather than period controls');
    assert(chartArea.firstElementChild === legend && chartArea.lastElementChild.contains(document.getElementById('memberPostChart')), 'legend sits immediately above the chart');
    assert(legend.classList.contains('mb-1') && legend.classList.contains('justify-content-end'), 'legend stays close to the upper-right chart edge');
    assert(total.querySelectorAll(':scope > thead > tr > th').length === 6, 'total table includes half-year achievement without engagement columns');
    assert(members.querySelectorAll(':scope > thead > tr > th').length === 6, 'achievement has its own column without engagement columns');
    assert(!/Good|閲覧/.test(document.getElementById('summary-pane').textContent), 'posting tab omits engagement figures and explanations');
    const totalColumns = Array.from(total.querySelectorAll(':scope > thead > tr > th'));
    const memberColumns = Array.from(members.querySelectorAll(':scope > thead > tr > th'));
    totalColumns.forEach((cell, index) => {
     assert(Math.abs(cell.getBoundingClientRect().width - memberColumns[index].getBoundingClientRect().width) < 1, 'both tables have identical column widths');
    });
    const toggle = members.querySelector('[data-quota-achievement] button[popovertarget]');
    const quota = document.getElementById(toggle.getAttribute('popovertarget'));
    const row = toggle.closest('tr');
    const before = row.getBoundingClientRect();
    const widths = Array.from(row.cells, cell => cell.getBoundingClientRect().width);
    assert(!quota.matches(':popover-open'), 'quota popup initially closed');
    toggle.click();
    assert(quota.matches(':popover-open'), 'quota popup opens on click');
    assert(quota.querySelector('.summary-quota-popup-header'), 'styled popup header');
    assert(row.getBoundingClientRect().height === before.height, 'opening quota does not change row height');
    Array.from(row.cells).forEach((cell, index) => assert(cell.getBoundingClientRect().width === widths[index], 'opening quota does not change column width'));
    quota.querySelector('[popovertargetaction="hide"]').click();
    assert(!quota.matches(':popover-open'), 'close button hides quota popup');
    const detailRows = [document.getElementById('summaryMonthly'), document.getElementById('memberMonthly1')];
    detailRows.forEach(detailRow => {
     // Bootstrap is not loaded in this isolated fixture: expose its collapse target for layout checks.
     detailRow.classList.remove('collapse');
     detailRow.querySelectorAll('details').forEach(detail => {detail.open = true;});
    const tables = Array.from(detailRow.querySelectorAll('table')).filter(table => !table.closest('[hidden]'));
     assert(tables.length === 3, 'selected period, current month and monthly breakdown tables');
     tables.forEach(table => {
      assert(getComputedStyle(table).tableLayout === 'fixed', 'detail tables use stable column widths');
      assert(table.getBoundingClientRect().width > 0, 'opened detail table has visible width');
     });
     const columns = tables.slice(0, 2).map(table => Array.from(table.querySelectorAll('thead th')));
     columns[0].forEach((cell, index) => {
      assert(Math.abs(cell.getBoundingClientRect().width - columns[1][index].getBoundingClientRect().width) < 1, 'approval and current-month columns align');
     });
    const panels = Array.from(detailRow.querySelector('.summary-posting-details').children);
    assert(panels.length === 2 && panels[0].querySelectorAll('.summary-detail-panel').length === 2, 'posting and current-month panels are stacked on the left');
    const wide = window.innerWidth >= 992;
    panels.forEach((panel, index) => {
     const rect = panel.getBoundingClientRect();
     const first = panels[0].getBoundingClientRect();
     if (wide) {
        assert(Math.abs(rect.top - first.top) < 1, 'monthly breakdown sits beside left stack');
        if (index) assert(rect.width > first.width, 'monthly breakdown receives more width');
      if (index) assert(rect.left > panels[index - 1].getBoundingClientRect().right, 'horizontal panels do not overlap');
     } else {
      assert(Math.abs(rect.left - first.left) < 1, 'narrow screen stacks detail panels');
     }
    });
     assert(tables[2].querySelector('th[rowspan="2"]'), 'monthly paired rows preserve month grouping');
    assert(tables[2].querySelectorAll('thead th').length === 5, 'monthly breakdown only displays posting and approval counts');
    const halfTabs = Array.from(detailRow.querySelectorAll('[role="tab"]'));
    assert(halfTabs.length >= 2, 'historical halves have separate tabs');
    halfTabs.forEach(tab => {
     tab.click();
     assert(tab.getAttribute('aria-selected') === 'true', 'half-year tab becomes selected');
     const panel = document.getElementById(tab.getAttribute('aria-controls'));
     assert(!panel.hidden, 'selected half-year panel is visible');
     assert(panel.querySelectorAll('tbody th[rowspan="2"]').length === 6, 'every half-year has six month slots');
     assert(detailRow.querySelectorAll('.summary-half-panel:not([hidden])').length === 1, 'only one half is visible');
    });
    halfTabs[0].click();
    halfTabs[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
    assert(halfTabs[1].getAttribute('aria-selected') === 'true', 'arrow key switches half-year tabs');
    const infoButtons = detailRow.querySelectorAll('.summary-detail-info');
    assert(infoButtons.length === 3, 'each detail panel has an information button');
    infoButtons.forEach(button => {
     const help = document.getElementById(button.getAttribute('popovertarget'));
     assert(!help.matches(':popover-open'), 'detail comments are hidden initially');
     const before = detailRow.getBoundingClientRect().height;
     button.click();
     assert(help.matches(':popover-open'), 'information button opens detail explanation');
     assert(detailRow.getBoundingClientRect().height === before, 'explanations do not shift detail layout');
     help.querySelector('[popovertargetaction="hide"]').click();
     assert(!help.matches(':popover-open'), 'detail explanation can be closed');
    });
    });
    totalColumns.forEach((cell, index) => {
     assert(Math.abs(cell.getBoundingClientRect().width - memberColumns[index].getBoundingClientRect().width) < 1, 'expanded details preserve main column alignment');
    });
     const meaning = document.getElementById('summary-counting-help');
     assert(!meaning.open, 'count definitions are initially collapsed');
     meaning.querySelector('summary').click();
     assert(meaning.open, 'count definitions can be opened');
    }
  assert(!document.getElementById('target-xss') && !window.xss, 'JSON script must escape script terminator');
  const payload = JSON.parse(document.getElementById('summary-posting-target').textContent);
  const labels = JSON.parse(document.getElementById('summary-chart-labels').textContent);
  if (!labels.length) {
   assert(instances.length === 0, 'empty data must not initialize a chart');
  } else {
   assert(instances.length === 1, 'one member chart');
   const config = instances[0].config;
   assert(config.type === 'bar' && config.options.scales.y.stacked && config.options.scales.x.stacked, 'stacked bar');
   assert(config.data.datasets[0].label === 'Q&A', 'no HTML entity label bug');
    assert(config.data.labels[0].includes('\x3c/script>'), 'literal unsafe label survives JSON parsing');
   const maximum = Math.max(config.data.datasets[0].data[0], payload.value || 0);
   assert(config.options.scales.y.suggestedMax === (maximum > 0 ? Math.ceil(maximum * 1.3) : 5), 'scale covers actual AND high target');
   const plugin = config.plugins.find(plugin => plugin.id === 'postingTargetLine');
   assert(!!plugin, 'actual chart registers target plugin');
   const calls = [];
   const ctx = {
    save() {calls.push(['save']);}, restore() {calls.push(['restore']);},
    beginPath() {}, moveTo(x, y) {calls.push(['move', x, y]);},
    lineTo(x, y) {calls.push(['line', x, y]);},
    stroke() {calls.push(['stroke', this.strokeStyle, this.lineWidth]);},
    fillText(text, x, y) {calls.push(['label', text, x, y, this.fillStyle]);}
   };
   const expectedY = 250 - (payload.value || 0) * 0.1;
   plugin.afterDatasetsDraw({ctx, chartArea: {left: 40, right: 640, top: 20, bottom: 250},
    scales: {y: {getPixelForValue(value) {assert(value === payload.value, 'scale receives numeric target'); return 250 - value * 0.1;}}}});
   if (payload.value === null) {
    assert(calls.length === 0, 'unset does not draw a line or label');
   } else {
    assert(calls.some(c => c[0] === 'move' && c[1] === 40 && c[2] === expectedY), 'left chart-area coordinate');
    assert(calls.some(c => c[0] === 'line' && c[1] === 640 && c[2] === expectedY), 'right chart-area coordinate');
    assert(calls.some(c => c[0] === 'stroke' && c[1] === '#dc2626' && c[2] === 2), 'red horizontal stroke');
    assert(calls.some(c => c[0] === 'label' && c[1] === payload.label + ': ' + payload.value + ' 件' && c[4] === '#dc2626'), 'red label without HTML entities');
    assert(calls[0][0] === 'save' && calls[calls.length - 1][0] === 'restore', 'canvas state restored');
   }
  }
    if (labels.length) {
     const chart = instances[0];
     const faq = document.querySelector('[data-summary-dataset="0"]');
     const tips = document.querySelector('[data-summary-dataset="1"]');
     assert(faq.tagName === 'BUTTON' && tips.tagName === 'BUTTON', 'keyboard-accessible legend controls');
     const totals = [];
     chart.ctx = {save() {}, restore() {}, fillText(value) {totals.push(value);}};
     const drawTotals = () => {
        totals.length = 0;
        chart.config.plugins.find(item => item.id === 'totalLabelPlugin').afterDatasetsDraw(chart);
        return totals.join();
     };
     faq.click();
     assert(!chart.isDatasetVisible(0) && chart.isDatasetVisible(1), 'FAQ legend hides only FAQ');
     assert(faq.getAttribute('aria-pressed') === 'false' && drawTotals() === '0', 'hidden FAQ excluded from totals and accessible state');
     tips.click();
     assert(!chart.isDatasetVisible(1) && drawTotals() === '', 'both hidden suppress total labels');
     faq.click();
     assert(chart.isDatasetVisible(0) && !chart.isDatasetVisible(1), 'FAQ can be restored independently');
     assert(drawTotals() === String(chart.data.datasets[0].data[0]), 'FAQ-only total');
     tips.click();
     assert(chart.isDatasetVisible(1) && tips.getAttribute('aria-pressed') === 'true', 'Tips restored');
     assert(chart.updates === 4 && instances.length === 1, 'legend reuses existing chart');
    }
    document.getElementById('result').textContent = 'PASS';
 } catch (error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
}, 0));
</script>'''
        with tempfile.TemporaryDirectory(prefix='nexus-summary-target-') as directory:
            page = Path(directory) / 'summary.html'
            page.write_text('<!doctype html><meta charset="utf-8">' + styles
                            + '<style>html,body {height:100%;margin:0;overflow:hidden;} #app-main {height:100vh;overflow:auto;}</style>'
                            + recording + '<main id="app-main">' + markup
                            + '<div style="height:1600px"></div></main>'
                            + '<pre id="result">PENDING</pre>' + checks, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', f'--window-size={width},1000', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=2000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            outcome = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertEqual(outcome, '<pre id="result">PASS', outcome or result.stderr[-2000:])

    def test_high_target_expands_axis_and_draws_at_scaled_y(self):
        self.run_browser(1000)

    def test_zero_target_draws_on_baseline_and_zero_actual_keeps_sane_axis(self):
        self.run_browser(0, actual=0)

    def test_unset_target_has_no_line_and_actual_still_controls_axis(self):
        self.run_browser(None, actual=20)

    def test_empty_registry_with_target_is_safe(self):
        self.run_browser(1000, empty=True)

    def test_narrow_screen_stacks_detail_panels(self):
        self.run_browser(1000, width=900)