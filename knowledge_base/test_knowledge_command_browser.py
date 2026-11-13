"""Offline display tests using the existing headless Chrome/Edge helper pattern.

Run directly or with unittest; no Django, database, server or extra dependencies.
"""

import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


class KnowledgeCommandBrowserTests(unittest.TestCase):
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
        root = Path(__file__).parent
        cls.assets = (root / 'templates/includes/knowledge_command_assets.html').read_text(encoding='utf-8')
        cls.base = (root / 'templates/base.html').read_text(encoding='utf-8')
        cls.base_css = re.search(r'<style>.*?</style>', cls.base, re.S).group(0)

    def run_checks(self, checks, *, setup='', content=None):
        if content is None:
            content = ('<div class="knowledge-section-body"><div id="command" class="knowledge-command" '
                       'style="color:rgb(120, 35, 90)"><code>  echo &lt;unsafe&gt; &amp; value\t '
                       '\n    <span style="color:rgb(18, 90, 30)">next</span>  \n</code></div></div>')
        fixture = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                   '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                   'script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; frame-src \'self\'">'
                   + self.base_css + '</head><body><form id="article">'
                   '<input id="focus" value="abcdef"><textarea id="saved">'
                   + html.escape(content) + '</textarea>' + content
                   + '</form><pre id="result">PENDING</pre><script>'
                   'window.savedHtml = document.getElementById("saved").value;'
                   'window.originalCode = document.querySelector("#command > code").innerHTML;'
                   'window.mutationBatches = 0;'
                   'const NativeObserver = window.MutationObserver;'
                   'window.MutationObserver = class extends NativeObserver {'
                   'constructor(callback) { super((records, observer) => {'
                   'window.mutationBatches++; callback(records, observer); }); }};'
                   + setup + '</script>')
        harness = r'''<script>
document.addEventListener('DOMContentLoaded', async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const command = document.getElementById('command');
    const code = command.querySelector(':scope > code');
    const button = () => command.querySelector(':scope > button.knowledge-command-copy');
    const notice = () => document.querySelector('.knowledge-command-notice');
    const setClipboard = value => Object.defineProperty(navigator, 'clipboard', {configurable:true, value});
    const checkSaved = () => {
        assert(document.getElementById('saved').value === savedHtml, 'saved HTML untouched');
        assert(code.innerHTML === originalCode, 'code markup untouched');
        assert(!/button|onclick|<script/i.test(savedHtml), 'saved HTML has no runtime controls or scripts');
        assert(!document.querySelector('.knowledge-command [onclick]'), 'no inline handlers created');
    };
    try {
        CHECKS
        checkSaved();
        document.getElementById('result').textContent = 'PASS: command display';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
    }
});
</script>'''.replace('CHECKS', checks)
        with tempfile.TemporaryDirectory(prefix='knowledge-command-browser-') as directory:
            page = Path(directory) / 'fixture.html'
            # Loading twice must not duplicate listeners, controls or observers.
            page.write_text(fixture + self.assets + self.assets + harness + '</body></html>', encoding='utf-8')
            result = subprocess.run([
                self.browser, '--headless=new', '--disable-gpu', '--no-sandbox',
                '--no-first-run', '--disable-background-networking',
                '--host-resolver-rules=MAP * ~NOTFOUND',
                f'--user-data-dir={directory}/profile', '--virtual-time-budget=8500',
                '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            report = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertIn('>PASS: command display', report, report or result.stderr[-2000:])

    def test_base_includes_asset_once_outside_scripts(self):
        includes = re.findall(r'{%\s*include\s+[\'"]includes/knowledge_command_assets.html[\'"]\s*%}', self.base)
        self.assertEqual(len(includes), 1)
        without_scripts = re.sub(r'<script\b[^>]*>.*?</script>', '', self.base, flags=re.S)
        self.assertIn(includes[0], without_scripts)
        self.assertLess(self.base.index(includes[0]), self.base.index('</body>'))

    def test_display_whitespace_colors_and_accessibility(self):
        self.run_checks(r'''
        assert(button() && button().type === 'button', 'runtime non-submit button');
        assert(button().getAttribute('aria-label') === 'コマンド行をコピー', 'accessible label');
        assert(button().querySelector('.bi-clipboard[aria-hidden="true"]'), 'decorative Bootstrap icon');
        assert(button().textContent === 'コピー', 'offline text fallback');
        assert(command.querySelectorAll(':scope > button').length === 1, 'one button after duplicate include');
        assert(getComputedStyle(command).backgroundColor === 'rgb(234, 244, 255)', 'pale blue background');
        assert(getComputedStyle(command).whiteSpace === 'pre-wrap', 'container preserves whitespace');
        assert(getComputedStyle(code).whiteSpace === 'pre-wrap', 'code preserves whitespace');
        assert(getComputedStyle(code).fontFamily.includes('monospace'), 'monospace font');
        assert(getComputedStyle(code).color === 'rgb(120, 35, 90)', 'chosen command color inherited');
        assert(getComputedStyle(code.querySelector('span')).color === 'rgb(18, 90, 30)', 'inline color retained');
        assert(getComputedStyle(button()).position === 'absolute', 'right-side control');
        assert(parseFloat(getComputedStyle(button()).right) >= 0, 'right inset');
        assert(notice().getAttribute('aria-live') === 'polite', 'live region exists before copying');
        ''')

    def test_clipboard_success_preserves_exact_text_and_resets(self):
        self.run_checks(r'''
        let copied, calls = 0, submissions = 0;
        document.getElementById('article').addEventListener('submit', event => { event.preventDefault(); submissions++; });
        setClipboard({writeText: async text => { copied = text; calls++; }});
        document.execCommand = () => { throw new Error('fallback must not run'); };
        const before = code.textContent;
        button().focus(); button().click(); await sleep(20);
        assert(copied === before && copied === '  echo <unsafe> & value\t \n    next  \n', 'exact whitespace copied');
        assert(!copied.includes('コピー') && calls === 1, 'no label or duplicate listener in copied text');
        assert(submissions === 0 && document.activeElement === button(), 'no submission or lost focus');
        assert(notice().textContent === 'コピーしました' && !notice().hidden, 'success announced');
        assert(button().querySelector('.bi-check-lg') && button().textContent === '✓', 'visible success');
        await sleep(2100);
        assert(button().querySelector('.bi-clipboard') && button().textContent === 'コピー', 'icon and label reset');
        ''')

    def test_fallback_without_clipboard_restores_input_selection(self):
        self.run_checks(r'''
        setClipboard(undefined);
        let copied, copies = 0;
        document.execCommand = name => {
            assert(name === 'copy' && document.activeElement.tagName === 'TEXTAREA', 'native textarea copy');
            copied = document.activeElement.value; copies++;
            assert(document.activeElement.selectionStart === 0 && document.activeElement.selectionEnd === copied.length, 'all whitespace selected');
            return true;
        };
        const input = document.getElementById('focus'); input.focus(); input.setSelectionRange(1, 4, 'backward');
        button().click(); await sleep(20);
        assert(copies === 1 && copied === code.textContent, 'fallback exact text');
        assert(document.activeElement === input && input.selectionStart === 1 && input.selectionEnd === 4 && input.selectionDirection === 'backward', 'input focus and selection restored');
        assert(document.querySelectorAll('textarea').length === 1, 'temporary textarea removed');
        assert(notice().textContent === 'コピーしました', 'fallback success');
        ''')

    def test_rejected_clipboard_uses_fallback_and_restores_dom_selection(self):
        self.run_checks(r'''
        setClipboard({writeText: async () => { throw new Error('denied'); }});
        let copied;
        document.execCommand = () => { copied = document.activeElement.value; return true; };
        const selection = window.getSelection(), range = document.createRange();
        range.setStart(code.firstChild, 2); range.setEnd(code.firstChild, 6);
        selection.removeAllRanges(); selection.addRange(range);
        const selected = selection.toString();
        button().click(); await sleep(20);
        assert(copied === code.textContent && selection.toString() === selected, 'rejection falls back and restores DOM selection');
        assert(notice().textContent === 'コピーしました', 'rejected API can still succeed');
        ''')

    def test_total_failure_selects_only_code_and_reports_failure(self):
        for unavailable, throws in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(clipboard_absent=unavailable, exec_command_throws=throws):
                self.run_checks(r'''
                setClipboard(CLIPBOARD);
                document.execCommand = () => { FALLBACK };
                button().click(); await sleep(20);
                assert(notice().textContent === 'コピーできませんでした', 'failure announced');
                const range = window.getSelection().getRangeAt(0);
                assert(range.startContainer === code && range.startOffset === 0 &&
                    range.endContainer === code && range.endOffset === code.childNodes.length, 'only code selected for manual copy');
                assert(document.querySelectorAll('textarea').length === 1, 'failed fallback cleaned up');
                assert(!button().hasAttribute('aria-busy'), 'control usable after failure');
                assert(!button().disabled && !button().querySelector('.bi-check-lg') &&
                    button().textContent === 'コピー', 'failure never shows success or disables control');
                let retry;
                setClipboard({writeText: async text => { retry = text; }});
                button().click(); await sleep(20);
                assert(retry === code.textContent && notice().textContent === 'コピーしました', 'retry works after failure');
                '''.replace('CLIPBOARD', 'undefined' if unavailable else
                            "{writeText: async () => { throw new Error('denied'); }}").replace(
                                'FALLBACK', "throw new Error('unsupported');" if throws else 'return false;'))

    def test_unicode_escaped_special_characters_and_separate_commands_do_not_bubble(self):
        self.run_checks(r'''
        const expected = ['  日本語 🧪 e\u0301\t<>&"\'  \n', '別コマンド\t  '];
        const commands = [...document.querySelectorAll('div.knowledge-command')];
        const copied = []; let parentClicks = 0;
        document.getElementById('article').addEventListener('click', () => parentClicks++);
        setClipboard({writeText: async text => copied.push(text)});
        for (let i = 0; i < commands.length; i++) {
            const controls = commands[i].querySelectorAll('button');
            assert(controls.length === 1, 'one control per independent command');
            controls[0].querySelector('span').click(); await sleep(20);
            assert(copied.length === i + 1 && copied[i] === expected[i], 'only clicked command exact Unicode and escaped text');
        }
        assert(parentClicks === 0, 'copy does not bubble to accordion/modal parent');
        assert(commands.every(node => node.querySelectorAll('button').length === 1), 'mutations never duplicate buttons');
        ''', content=('<div id="command" class="knowledge-command"><code>  日本語 🧪 e\u0301\t'
                      '&lt;&gt;&amp;&quot;&#39;  \n</code></div>'
                      '<div class="knowledge-command"><code>別コマンド\t  </code></div>'))

    def test_br_is_copied_as_newline_not_concatenated_text(self):
        # Independent literal oracle: textContent itself omits BR newlines.
        self.run_checks(r'''
        let copied;
        setClipboard({writeText: async text => { copied = text; }});
        button().click(); await sleep(20);
        assert(copied === '  一行目\t\n二行目 & <値>\n\n末尾  ',
            'BR must copy newline: actual=' + JSON.stringify(copied));
        ''', content=('<div id="command" class="knowledge-command"><code>  一行目\t<br>'
                      '<span>二行目 &amp; &lt;値&gt;</span><br><br>末尾  </code></div>'))

    def test_editor_sources_and_tinymce_containers_never_gain_copy_controls(self):
        self.run_checks(r'''
        const containers = document.querySelectorAll('[data-editor-fixture]');
        const before = [...containers].map(node => node.innerHTML);
        await sleep(30);
        containers.forEach((node, i) => {
            assert(!node.querySelector('button'), 'editable and TinyMCE surfaces ignored');
            assert(node.innerHTML === before[i], 'editor DOM serialization untouched');
        });
        const source = document.getElementById('saved');
        source.dispatchEvent(new Event('input', {bubbles:true})); await sleep(20);
        assert(source.value === savedHtml && !source.value.includes('knowledge-command-copy'), 'submitted source never gains controls');
        ''', content=('<div id="command" class="knowledge-command"><code>display only</code></div>'
                      '<div data-editor-fixture contenteditable="true"><div class="knowledge-command"><code>inherited editable</code></div></div>'
                      '<div data-editor-fixture class="knowledge-command" contenteditable="true"><code>direct editable</code></div>'
                      '<div data-editor-fixture class="tox-tinymce"><div class="knowledge-command"><code>editor</code></div></div>'
                      '<div data-editor-fixture class="tox-tinymce-aux"><div class="knowledge-command"><code>aux</code></div></div>'))

    def test_dynamic_preview_direct_code_scope_and_no_observer_loop(self):
        self.run_checks(r'''
        const preview = document.createElement('section');
        preview.innerHTML = '<div class="knowledge-command"><code> dynamic\t\n </code></div>' +
            '<div class="knowledge-command"><span><code>not direct</code></span></div>' +
            '<p class="knowledge-command"><code>not div</code></p><pre><code>ordinary code</code></pre>' +
            '<div contenteditable="true"><div class="knowledge-command"><code>editor</code></div></div>';
        document.body.append(preview); await sleep(30);
        assert(preview.querySelectorAll('button').length === 1, 'only div with direct code enhanced, not editor');
        let copied; setClipboard({writeText: async text => { copied = text; }});
        preview.querySelector('button').click(); await sleep(20);
        assert(copied === ' dynamic\t\n ', 'dynamic command copies exact whitespace');
        const late = document.createElement('div'); late.className = 'knowledge-command'; preview.append(late);
        await sleep(20); assert(!late.querySelector('button'), 'missing code not initialized');
        const lateCode = document.createElement('code'); lateCode.textContent = 'late'; late.append(lateCode);
        await sleep(20); assert(late.querySelectorAll('button').length === 1, 'code arriving later initialized');
        const oldButton = late.querySelector('button'); oldButton.remove();
        await sleep(20); assert(late.querySelectorAll('button').length === 1, 'removed runtime button restored once');
        const iframe = document.createElement('iframe'); document.body.append(iframe);
        const inside = iframe.contentDocument.createElement('div'); inside.className = 'knowledge-command';
        const insideCode = iframe.contentDocument.createElement('code'); insideCode.textContent = 'TinyMCE';
        inside.append(insideCode); iframe.contentDocument.body.append(inside);
        await sleep(30); assert(!inside.querySelector('button'), 'iframe content not traversed');
        const batches = mutationBatches; await sleep(100);
        assert(mutationBatches === batches && batches < 40, 'observer settles without self-triggered loops');
        ''')

    def test_dark_theme_contrast_and_chosen_color(self):
        self.run_checks(r'''
        document.documentElement.setAttribute('data-bs-theme', 'dark');
        command.style.removeProperty('color');
        const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
        const luminance = value => rgb(value).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
            .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        const contrast = (a, b) => { const x = luminance(a), y = luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); };
        const style = getComputedStyle(command), control = getComputedStyle(button());
        assert(contrast(getComputedStyle(code).color, style.backgroundColor) >= 4.5, 'dark command AA contrast');
        assert(contrast(control.color, control.backgroundColor) >= 4.5, 'dark button AA contrast');
        command.style.color = 'rgb(240, 220, 100)';
        assert(getComputedStyle(code).color === 'rgb(240, 220, 100)', 'chosen color retained in dark mode');
        assert(getComputedStyle(code.querySelector('span')).color === 'rgb(18, 90, 30)', 'nested chosen color not overwritten');
        ''')


if __name__ == '__main__':
    unittest.main()