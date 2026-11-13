"""Offline section layout regression tests; no Django, DB, server or CDN.

Uses the same isolated headless Chrome/Edge pattern as the command display tests.
Only real template styles are loaded, including later legacy FAQ selectors.
"""

import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


class KnowledgeSectionLayoutBrowserTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        candidates = [
            shutil.which('chromium'), shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
        cls.browser = next((p for p in candidates if p and os.path.isfile(p)), None)
        if cls.browser is None:
            raise unittest.SkipTest('Chrome/Edge/Chromium is required')
        base = (Path(__file__).parent / 'templates/base.html').read_text(encoding='utf-8')
        # Preserve the entire cascade and source order, not just section rules.
        cls.styles = '\n'.join(re.findall(r'<style\b[^>]*>.*?</style>', base, re.S))
        if not cls.styles:
            raise AssertionError('No base template styles found')

    def run_checks(self, checks):
        section = '''<div class="knowledge-content-section">
<h3><span data-heading>最初の文字から揃える見出し</span></h3>
<div class="knowledge-section-body">
<p><span data-paragraph>通常段落の本文</span></p>
<p style="margin-left: 3em; padding-left: 2em; text-indent: 1em"><span data-paragraph>旧インライン字下げ付きの段落</span></p>
<ul><li><span data-top>最上位の箇条書き</span>
  <ol><li><span data-nested>二段目の番号</span>
    <ul><li><span data-nested>三段目の箇条書き</span></li></ul>
  </li></ol></li>
  <li><p><span data-top>段落で包んだ箇条書き</span></p></li>
  <li><span data-wrap>折り返す箇条書きの本文を十分長く記載して確認します。''' + (
            '折り返し後も本文の開始位置を保ちます。' * 12
        ) + '''</span></li></ul>
<ol start="10"><li><span data-top>最上位の二桁番号</span>
  <ul><li><span data-nested>二段目の箇条書き</span>
    <ol><li><span data-nested>三段目の番号</span></li></ol>
  </li></ul></li>
  <li><div style="font-weight: 400; text-indent: 1em"><span data-top>divで包んだ番号本文</span></div></li></ol>
</div></div>'''
        contexts = ('', 'faq-body-indent', 'accordion-body', 'faq-question')
        fixtures = []
        for context in contexts:
            content = section
            if context in ('accordion-body', 'faq-question'):
                content = '<div class="faq-body-indent">' + content + '</div>'
            fixtures.append('<div data-context="' + context + '" class="' + context + '">'
                            + content + '</div>')
        legacy = '''<div class="accordion-body"><div class="faq-body-indent" id="legacy">
<h3>旧形式の見出し</h3>
<div class="tk-heading1-line" style="font-weight: 700">旧形式の太字見出し</div>
<div style="font-weight: 400">旧形式の字下げ本文</div><p>旧形式の段落</p>
<ul><li>旧箇条書き<ol><li><div style="font-weight: 400">旧ネスト本文</div></li></ol></li></ul>
<ol start="10"><li>旧番号<ul><li>旧ネスト箇条書き</li></ul></li></ol>
</div></div>'''
        harness = r'''<script>
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const close = (actual, expected, message) => assert(Math.abs(actual - expected) <= .1,
    `${message}: actual=${actual}, expected=${expected}`);
const px = value => parseFloat(value);
// Measure actual first-character text boxes, not the h3/list element edges.
const textX = element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const offset = node.textContent.search(/\S/);
        if (offset < 0) continue;
        const range = document.createRange();
        range.setStart(node, offset); range.setEnd(node, offset + 1);
        return range.getBoundingClientRect().x;
    }
    throw new Error('Missing fixture text');
};
const eachLayout = check => {
    for (const rootSize of [16, 20]) {
        document.documentElement.style.fontSize = rootSize + 'px';
        for (const width of [320, 1024]) {
            document.querySelectorAll('[data-context]').forEach(context => {
                context.style.width = width + 'px';
                const section = context.querySelector('.knowledge-content-section');
                const body = section.querySelector('.knowledge-section-body');
                const label = `${context.dataset.context || 'standalone'}, width=${width}, root=${rootSize}`;
                check(section, body, label, rootSize);
            });
        }
    }
};
try {
CHECKS
    document.getElementById('result').textContent = 'PASS: section layout';
} catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
}
</script>'''.replace('CHECKS', checks)
        fixture = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                   '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                   'script-src \'unsafe-inline\'; style-src \'unsafe-inline\'">'
                   + self.styles + '</head><body><main>' + ''.join(fixtures) + legacy
                   + '</main><pre id="result">PENDING</pre>' + harness + '</body></html>')
        with tempfile.TemporaryDirectory(prefix='knowledge-section-layout-browser-') as directory:
            page = Path(directory) / 'fixture.html'
            page.write_text(fixture, encoding='utf-8')
            result = subprocess.run([
                self.browser, '--headless=new', '--disable-gpu', '--no-sandbox',
                '--no-first-run', '--disable-background-networking',
                '--host-resolver-rules=MAP * ~NOTFOUND',
                f'--user-data-dir={directory}/profile', '--virtual-time-budget=1500',
                '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            report = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
            self.assertIsNotNone(report, result.stderr[-2000:])
            self.assertEqual(html.unescape(report.group(1)), 'PASS: section layout')

    def test_heading_paragraph_and_top_level_list_text_x_match(self):
        self.run_checks(r'''
eachLayout((section, body, label, rootSize) => {
    const heading = section.querySelector('[data-heading]');
    const x = textX(heading);
    close(x - section.getBoundingClientRect().x, rootSize, label + ': heading 1rem');
    close(px(getComputedStyle(body).paddingLeft), rootSize, label + ': body left 1rem');
    close(px(getComputedStyle(body).paddingRight), rootSize, label + ': body right 1rem');
    body.querySelectorAll('[data-paragraph], [data-top]').forEach(text =>
        close(textX(text), x, label + ': ' + text.textContent));
    const wrapped = body.querySelector('[data-wrap]');
    const range = document.createRange(); range.selectNodeContents(wrapped);
    const lines = [...range.getClientRects()];
    assert(lines.length > 1, label + ': fixture must actually wrap');
    lines.forEach(line => close(line.x, x, label + ': wrapped line'));
    body.querySelectorAll(':scope > ul, :scope > ol').forEach(list => {
        close(px(getComputedStyle(list).paddingLeft), 0, label + ': top-level padding');
        close(px(getComputedStyle(list).marginLeft), 0, label + ': top-level margin');
    });
});
''')

    def test_outside_markers_and_mixed_three_level_nesting_are_preserved(self):
        self.run_checks(r'''
eachLayout((section, body, label) => {
    body.querySelectorAll('ul, ol').forEach(list => {
        const style = getComputedStyle(list);
        assert(style.listStylePosition === 'outside', label + ': outside marker');
        assert(style.listStyleType !== 'none', label + ': visible list type');
        [...list.children].forEach(li => {
            const itemStyle = getComputedStyle(li);
            assert(itemStyle.display === 'list-item', label + ': real list-item');
            assert(itemStyle.listStylePosition === 'outside', label + ': item outside marker');
            assert(getComputedStyle(li, '::marker').content !== '""', label + ': marker not suppressed');
        });
        if (list.parentElement !== body) {
            const parentItem = list.parentElement;
            const indent = 1.5 * px(style.fontSize);
            close(px(style.paddingLeft), indent, label + ': nested 1.5em padding');
            close(textX(list.querySelector('li')), textX(parentItem) + indent,
                label + ': nested text advances one level');
        }
    });
    assert(body.querySelectorAll('[data-nested]').length === 4, label + ': mixed three-level coverage');
});
''')

    def test_legacy_layout_unchanged_by_section_alignment_rules(self):
        self.run_checks(r'''
const legacy = document.getElementById('legacy');
const snapshot = () => [legacy, ...legacy.querySelectorAll('*')].map(node => {
    const style = getComputedStyle(node);
    return [node.tagName, node.getBoundingClientRect().x, node.getBoundingClientRect().width,
        ...['marginLeft', 'marginRight', 'paddingLeft', 'paddingRight', 'textIndent',
            'fontSize', 'lineHeight', 'listStylePosition', 'listStyleType', 'borderBottomWidth']
            .map(key => style[key]), getComputedStyle(node, '::marker').content,
        getComputedStyle(node, '::before').content, textX(node)];
});
const before = snapshot();
close(px(getComputedStyle(legacy).marginLeft), 96 / 2.54, 'legacy 1cm outer margin');
legacy.querySelectorAll('ul, ol').forEach(list => {
    const style = getComputedStyle(list);
    close(px(style.paddingLeft), 2.5 * px(style.fontSize), 'legacy 2.5em list padding');
    assert(style.listStylePosition === 'outside', 'legacy outside markers');
});
const oldParagraph = legacy.querySelector(':scope > div[style*="400"]');
close(px(getComputedStyle(oldParagraph).textIndent), px(getComputedStyle(oldParagraph).fontSize),
    'legacy normal-weight div keeps 1em indent');
assert(getComputedStyle(legacy.querySelector('.tk-heading1-line'), '::before').content.includes('■'),
    'legacy heading marker preserved');
// Recreate the pre-change CSS in memory only. Compare the same legacy DOM,
// with and without this change; no repository baseline or app/DB is needed.
let removed = 0, headings = 0;
for (const sheet of document.styleSheets) {
    for (let i = sheet.cssRules.length - 1; i >= 0; i--) {
        const rule = sheet.cssRules[i];
        if (!rule.selectorText) continue;
        if (rule.selectorText === '.knowledge-content-section > h3') {
            rule.style.setProperty('padding', '.65rem 1em .35em', 'important');
            headings++;
        } else if (rule.selectorText.includes('knowledge-section-body') &&
                 !rule.selectorText.includes('blockquote') &&
                   (rule.style.paddingLeft || rule.style.textIndent)) {
            sheet.deleteRule(i); removed++;
        }
    }
}
assert(headings === 1 && removed === 4, `baseline rules found: headings=${headings}, added=${removed}`);
assert(JSON.stringify(snapshot()) === JSON.stringify(before), 'legacy layout/styles equal pre-change CSS');
''')


if __name__ == '__main__':
    unittest.main()