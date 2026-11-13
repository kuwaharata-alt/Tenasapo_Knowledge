"""Offline Chrome tests of the real shared asset, without Django or database access."""

import html
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


TINY_STUB = r"""
window.tinyInstances = new Map();
window.tinyOptions = [];
window.tinymce = {
    init(options) {
        tinyOptions.push(options);
        const listeners = new Map();
        let content = options.target.value, dirty = false, focused = false;
        const editor = {
            id: options.target.id, removed: false,
            on(names, callback) { names.split(' ').forEach(name => {
                if (!listeners.has(name)) listeners.set(name, []);
                listeners.get(name).push(callback);
            }); },
            emit(name, event = {type: name}) { (listeners.get(name) || []).forEach(callback => callback(event)); },
            getContent() { return content; },
            setContent(value) { content = value; this.emit('change'); },
            isDirty() { return dirty; }, setDirty(value) { dirty = value; },
            focus() { focused = true; this.emit('focus'); },
            hasFocus() { return focused; },
            selection: { getBookmark() { return {position: content.length}; },
                moveToBookmark(bookmark) { editor.restoredBookmark = bookmark; } },
            insertContent(value) { content += value; dirty = true; this.emit('input'); },
            edit(value, event = 'input') { content = value; dirty = true; this.emit(event); },
            remove() { this.removed = true; options.target.style.display = ''; tinyInstances.delete(this.id); }
        };
        tinyInstances.set(editor.id, editor);
        options.setup(editor);
        if (window.tinyMode === 'reject') return Promise.reject(new Error('offline'));
        if (window.tinyMode === 'hang') return new Promise(() => {});
        return new Promise(resolve => setTimeout(() => {
            if (!editor.removed || window.tinyMode === 'late') { options.target.style.display = 'none'; editor.emit('init'); }
            resolve([editor]);
        }, window.tinyMode === 'late' ? 5500 : window.tinyMode === 'delay' ? 100 : 0));
    },
    triggerSave() { tinyInstances.forEach(editor => { document.getElementById(editor.id).value = editor.getContent(); }); }
};
"""


class KnowledgeBlockEditorBrowserTests(unittest.TestCase):
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
        cls.assets = (Path(__file__).parent / 'templates/includes/knowledge_block_editor_assets.html').read_text(encoding='utf-8')

    def run_checks(self, checks, *, original='', tiny=False, mode='normal', source_name='body'):
        fixture = '''<!doctype html><meta charset="utf-8"><form id="article">
<label for="original">Original body</label><textarea id="original" name="''' + html.escape(source_name, quote=True) + '''" required data-wysiwyg="true">''' + html.escape(original) + '''</textarea>
<textarea id="other" name="other" data-wysiwyg="true"></textarea>
<button id="submit" type="submit">Save</button></form><pre id="result">PENDING</pre>'''
        setup = '<script>window.tinyMode = ' + repr(mode) + ';' + (TINY_STUB if tiny else '') + '</script>'
        harness = r'''<script>
document.addEventListener('DOMContentLoaded', async () => {
    const api = window.KnowledgeBlockEditors;
    const source = document.getElementById('original'), form = document.getElementById('article');
    const root = document.querySelector('[data-knowledge-editor="original"]');
    const cards = () => [...root.querySelectorAll('.knowledge-block-card')];
    const body = card => card.querySelector('textarea');
    const heading = card => card.querySelector('input');
    const action = (card, name) => card.querySelector('[data-action="' + name + '"]');
    const add = () => root.lastElementChild.click();
    const edit = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', {bubbles:true})); };
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const initial = source.value;
    try {
        CHECKS
        document.getElementById('result').textContent = 'PASS: block editor';
    } catch (error) { document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message); }
});
</script>'''.replace('CHECKS', checks)
        with tempfile.TemporaryDirectory(prefix='knowledge-block-browser-') as directory:
            page = Path(directory) / 'fixture.html'
            page.write_text(fixture + setup + self.assets + self.assets + harness, encoding='utf-8')
            result = subprocess.run([
                self.browser, '--headless=new', '--disable-gpu', '--no-sandbox',
                '--no-first-run', '--disable-background-networking',
                f'--user-data-dir={directory}/profile', '--virtual-time-budget=8500',
                '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            report = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertIn('>PASS: block editor', report, report or result.stderr[-2000:])

    def test_heading_shortcuts_for_question_answer_and_tips(self):
        original = ('<div class="knowledge-content-section"><h3>既存の見出し</h3>'
                    '<div class="knowledge-section-body"><p>本文を保持</p></div></div>')
        for source_name in ('question', 'answer', 'body'):
            for tiny in (False, True):
                with self.subTest(source_name=source_name, tiny=tiny):
                    self.run_checks(r'''
                    await sleep(30);
                    const expected = source.name === 'question' ? ['質問'] : ['概要', '手順', '注意事項', '備考'];
                    const shortcuts = card => [...card.querySelectorAll('.knowledge-block-heading-shortcuts button')];
                    assert(api.getHtml('original') === initial, 'shortcuts do not rewrite existing content');
                    assert(shortcuts(cards()[0]).map(button => button.textContent).join(',') === expected.join(','), 'correct shortcuts for field');
                    assert(!document.querySelector('[data-knowledge-editor="other"] .knowledge-block-heading-shortcuts'), 'unrelated field has no shortcuts');
                    let submissions = 0, updates = 0;
                    form.addEventListener('submit', event => { event.preventDefault(); submissions++; });
                    source.addEventListener('input', () => updates++);
                    heading(cards()[0]).setAttribute('aria-invalid', 'true');
                    for (const button of shortcuts(cards()[0])) {
                        assert(button.type === 'button', 'shortcut is not a submit control');
                        button.click();
                        assert(heading(cards()[0]).value === button.textContent, 'click replaces heading with preset');
                        assert(document.activeElement === heading(cards()[0]), 'heading receives focus');
                        assert(!heading(cards()[0]).hasAttribute('aria-invalid'), 'heading error cleared');
                        assert(source.value.includes('<h3>' + button.textContent + '</h3>'), 'source synchronized immediately');
                        assert(source.value.includes('<p>本文を保持</p>'), 'body preserved');
                    }
                    assert(updates === expected.length && submissions === 0, 'changes dispatched without form submission');
                    edit(heading(cards()[0]), '自由入力');
                    assert(source.value.includes('<h3>自由入力</h3>'), 'custom heading still supported');
                    add();
                    assert(shortcuts(cards()[1]).length === expected.length, 'new section has matching shortcuts');
                    shortcuts(cards()[1])[0].click();
                    assert(heading(cards()[1]).value === expected[0], 'new section shortcut works');
                    assert(heading(cards()[0]).value === '自由入力', 'other section unchanged');
                    ''', original=original, tiny=tiny, source_name=source_name)

    def test_paste_inserts_plain_text_without_clipboard_formatting(self):
        for tiny in (False, True):
            with self.subTest(tiny=tiny):
                self.run_checks(r'''
                await sleep(30);
                const input = body(cards()[0]);
                const transfer = new DataTransfer();
                transfer.setData('text/plain', '貼り付け <script> & text\r\n次の行');
                transfer.setData('text/html', '<b style="color:red">WRONG FORMAT</b>');
                const event = new ClipboardEvent('paste', {clipboardData: transfer, cancelable: true, bubbles: true});
                if (window.tinymce) {
                    assert(tinyOptions[0].paste_as_text === true, 'TinyMCE plain text mode enabled');
                    tinyInstances.get(input.id).emit('paste', event);
                } else {
                    input.value = 'beforeAFTER'; input.setSelectionRange(6, 11);
                    input.dispatchEvent(event);
                    assert(input.value.startsWith('before'), 'selection replacement preserves prefix');
                    assert(!input.value.includes('AFTER'), 'selection replaced');
                }
                const value = api.getHtml('original');
                assert(event.defaultPrevented, 'default rich paste prevented');
                assert(value.includes('貼り付け &lt;script&gt; &amp; text<br>次の行'), 'escaped text and line breaks retained');
                assert(!value.includes('WRONG FORMAT') && !value.includes('<script>'), 'clipboard HTML never inserted');
                assert(api.hasContent('original'), 'paste synchronizes source');
                ''', tiny=tiny)

    def test_native_fallback_structure_events_validation_and_submit(self):
        self.run_checks(r'''
        assert(source.hidden && source.id === 'original' && source.name === 'body', 'source identity retained');
        assert(!source.required, 'hidden source does not block native submit');
        assert(cards().length === 1 && !body(cards()[0]).hidden, 'one visible native fallback');
        assert(action(cards()[0], 'remove').disabled, 'last block remains');
        const ids = [...document.querySelectorAll('[id]')].map(n => n.id);
        assert(new Set(ids).size === ids.length, 'all IDs unique');
        root.querySelectorAll('label').forEach(label => assert(document.getElementById(label.htmlFor), 'labels resolve'));
        assert(!root.querySelector('[name]'), 'internal controls have no submitted names');
        assert(!api.hasContent('original'), 'empty body initially empty');
        let updates = 0, states = [];
        source.addEventListener('input', () => updates++);
        document.addEventListener('knowledge-editor-state', event => { if (event.detail.id === 'original') states.push(event.detail); });
        heading(cards()[0]).focus();
        edit(heading(cards()[0]), '<img src=x onerror=alert(1)>');
        assert(!api.hasContent('original'), 'heading only is not body content');
        assert(api.getHtml('original').includes('&lt;img'), 'heading is escaped');
        edit(body(cards()[0]), '<p><br></p>');
        assert(!api.hasContent('original'), 'empty markup is not content');
        edit(body(cards()[0]), '<img src="/image.png" alt="image">');
        assert(api.hasContent('original'), 'image counts as body content');
        assert(updates >= 3 && states.some(state => state.focused), 'source input and owner focus state dispatched');
        source.value = 'stale';
        let submitted;
        form.addEventListener('submit', event => { event.preventDefault(); submitted = new FormData(form); });
        form.requestSubmit();
        assert(submitted.get('body') === api.getHtml('original'), 'capture submit sync precedes old listeners');
        assert([...submitted.keys()].sort().join(',') === 'body,other', 'only original source names submitted');
        assert(api.ownerId(body(cards()[0]).id) === 'original', 'native ownership');
        assert(api.ownerId('missing') === null, 'unknown editor ownership null');
        ''')

    def test_legacy_html_plaintext_and_bbcode_untouched(self):
        for original in [
            '<H2 class="old">Legacy</H2>\n<p style="color:red">Text &amp; text</p><img src="/x.png">',
            '[b]Legacy[/b]\n<script-looking but plain & text',
            'plain text\nsecond line < & >',
        ]:
            with self.subTest(original=original):
                self.run_checks(r'''
                await sleep(20);
                assert(cards().length === 1, 'legacy is one block');
                assert(heading(cards()[0]).value === '内容', 'meaningful legacy heading suggestion');
                assert(api.getHtml('original') === initial && api.sync('original') === initial, 'byte exact legacy get and sync');
                if (window.tinymce) {
                    const editor = tinyInstances.get(body(cards()[0]).id);
                    editor.setContent('<p>normalized initialization</p>');
                    editor.emit('change');
                    assert(api.getHtml('original') === initial, 'non-dirty setContent never marks user edits');
                }
                ''', original=original, tiny=True)

    def test_exact_wrappers_unknown_outside_and_unchanged_body(self):
        original = ('<aside data-old="1">Outside</aside>'
                    '<div class="knowledge-content-section"><h3>One &amp; two</h3>'
                    '<div class="knowledge-section-body"><P class="old">Body &amp; <IMG src="/i.png"></P></div></div>'
                    '<div class="knowledge-content-section extra">Not a wrapper</div>')
        self.run_checks(r'''
        assert(cards().length === 3, 'only exact class div recognized; outside HTML retained');
        assert(api.getHtml('original') === initial, 'all raw HTML exact initially');
        edit(heading(cards()[1]), '<new & heading>');
        const html = api.getHtml('original');
        assert(html.includes('<P class="old">Body &amp; <IMG src="/i.png"></P>'), 'heading edit preserves exact body bytes');
        assert(html.startsWith('<aside data-old="1">Outside</aside>'), 'unknown prefix preserved');
        assert(html.endsWith('<div class="knowledge-content-section extra">Not a wrapper</div>'), 'unknown suffix preserved');
        assert(html.includes('&lt;new &amp; heading&gt;'), 'heading escaped');
        ''', original=original)

    def test_add_move_remove_confirmation_and_markers(self):
        self.run_checks(r'''
        edit(body(cards()[0]), '<p>first</p>');
        add(); edit(body(cards()[1]), '<p>second</p>');
        action(cards()[1], 'up').click();
        assert(api.getHtml('original').indexOf('second') < api.getHtml('original').indexOf('first'), 'move up order');
        action(cards()[0], 'down').click();
        assert(api.getHtml('original').indexOf('first') < api.getHtml('original').indexOf('second'), 'move down order');
        let confirmations = 0;
        window.confirm = () => { confirmations++; return false; };
        action(cards()[1], 'remove').click();
        assert(cards().length === 2 && confirmations === 1, 'nonempty removal cancel');
        window.confirm = () => true;
        const removedId = body(cards()[1]).id;
        action(cards()[1], 'remove').click();
        assert(cards().length === 1 && api.ownerId(removedId) === null, 'confirmed removal cleans ownership');
        add();
        window.confirm = () => { throw new Error('empty remove should not confirm'); };
        action(cards()[1], 'remove').click();
        assert(cards().length === 1, 'empty block removal');
        const input = body(cards()[0]); input.focus(); input.setSelectionRange(3, 3);
        assert(api.insertMarker('original', '[IMAGE:<1>]'), 'marker insertion supported');
        assert(api.getHtml('original').includes('[IMAGE:&lt;1&gt;]'), 'marker safe and retained');
        assert(api.focus('original') && document.activeElement === input, 'fallback focus');
        assert(!api.insertMarker('missing', 'x'), 'unknown source insertion false');
        ''')

    def test_required_headings_in_every_block_native_and_rich_editor(self):
        for tiny in (False, True):
            with self.subTest(tiny=tiny):
                self.run_checks(r'''
                await sleep(30);
                const other = document.querySelector('[data-knowledge-editor="other"]');
                edit(other.querySelector('input'), 'Other');
                edit(other.querySelector('textarea'), '<p>Other body</p>');
                edit(heading(cards()[0]), 'First');
                if (window.tinymce) tinyInstances.get(body(cards()[0]).id).edit('<p>First body</p>');
                else edit(body(cards()[0]), '<p>First body</p>');
                assert(api.validate(form), 'valid heading and body accepted');
                add(); await sleep(30);
                const second = cards()[1], title = heading(second);
                if (window.tinymce) tinyInstances.get(body(second).id).edit('<p>Second body</p>');
                else edit(body(second), '<p>Second body</p>');
                for (const value of ['', ' \t　\u200b\ufeff ']) {
                    edit(title, value);
                    assert(!api.validate(form), 'later blank heading rejected');
                    assert(document.activeElement === title, 'focus invalid heading, not hidden source or iframe');
                    assert(title.getAttribute('aria-invalid') === 'true', 'invalid state');
                }
                edit(title, 'Second');
                assert(api.validate(form) && !title.hasAttribute('aria-invalid'), 'corrected heading accepted');
                assert(root.querySelector('[role="alert"]').hidden, 'error cleared');
                assert(api.getHtml('original').includes('<h3>Second</h3>'), 'valid heading serialized');
                ''', tiny=tiny)

    def test_headingless_structured_block_rejected_but_legacy_changes_get_default_heading(self):
        structured = ('<div class="knowledge-content-section">'
                      '<div class="knowledge-section-body"><p>Body</p></div></div>')
        self.run_checks(r'''
        assert(heading(cards()[0]).value === '', 'structured missing heading never auto-filled');
        assert(!api.validate(form) && document.activeElement === heading(cards()[0]), 'existing structured block requires correction');
        assert(source.value === initial, 'invalid stored block is not silently rewritten');
        ''', original=structured)
        self.run_checks(r'''
        const other = document.querySelector('[data-knowledge-editor="other"]');
        edit(other.querySelector('input'), 'Other'); edit(other.querySelector('textarea'), '<p>Other</p>');
        assert(api.validate(form) && source.value === initial, 'legacy validation leaves original exact');
        edit(body(cards()[0]), '<p>Edited legacy</p>');
        assert(api.validate(form), 'body-only legacy edit remains possible');
        assert(api.getHtml('original').includes('<h3>内容</h3>'), 'default heading saved only with actual edit');
        ''', original='[b]Legacy[/b]\nKeep me')

    def test_tinymce_options_dirty_events_bookmark_sync_and_reorder(self):
        self.run_checks(r'''
        await sleep(30);
        let editor = tinyInstances.get(body(cards()[0]).id);
        assert(editor && api.ownerId(editor) === 'original', 'TinyMCE ownership keyed by internal ID');
        assert(tinyOptions.every(o => o.target.id !== 'original' && o.target.id !== 'other'), 'never initialize original IDs');
        const options = tinyOptions[0];
        assert(options.plugins === 'lists' && options.toolbar === 'undo redo | bold italic | bullist numlist', 'restricted plugins and toolbar');
        assert(options.height === 200 && options.min_height === 160 && options.resize, 'sizing');
        assert(!options.menubar && !options.statusbar && !options.branding && !options.promotion, 'no extra UI');
        assert(!options.language && options.valid_elements.includes('img[') && options.browser_spellcheck, 'offline-safe and retained rich markup');
        assert(api.getHtml('original') === initial, 'init normalization not a user edit');
        editor.edit('<p><strong>first</strong><img src="/i.png"></p>', 'change');
        assert(api.getHtml('original').includes('<strong>first</strong>'), 'dirty change serialized');
        editor.focus(); editor.emit('blur');
        api.insertMarker('original', '[image-1]');
        assert(editor.restoredBookmark && api.getHtml('original').includes('[image-1]'), 'active editor bookmark insertion');
        add(); await sleep(20);
        const second = tinyInstances.get(body(cards()[1]).id);
        second.edit('<p>second</p>', 'Undo');
        // A dirty value without an input/change event must survive iframe recreation.
        second.setContent('<p>second unsignalled</p>'); second.setDirty(true);
        action(cards()[1], 'up').click(); await sleep(20);
        assert(api.getHtml('original').includes('second unsignalled'), 'reorder flushes pending dirty content');
        assert(api.getHtml('original').indexOf('second') < api.getHtml('original').indexOf('first'), 'TinyMCE move preserves body');
        const current = tinyInstances.get(body(cards()[0]).id);
        current.edit('<p>redo value</p>', 'Redo');
        assert(api.getHtml('original').includes('redo value'), 'recreated editor accepts changes');
        source.value = 'old'; tinymce.triggerSave(); api.syncForm(form);
        assert(source.value === api.getHtml('original'), 'triggerSave never overwrites original source');
        assert(!root.querySelector('[name]'), 'TinyMCE sources nameless');
        ''', original='<p>legacy</p>', tiny=True)

    def test_pending_native_edits_rejection_timeout_and_late_resolution(self):
        for mode in ['reject', 'hang', 'delay', 'late']:
            with self.subTest(mode=mode):
                self.run_checks(r'''
                const input = body(cards()[0]);
                assert(!input.hidden && input.style.display !== 'none', 'native fallback visible while initializing');
                edit(input, '<p>typed while loading</p>');
                await sleep(window.tinyMode === 'hang' || window.tinyMode === 'late' ? 5800 : 150);
                assert(api.getHtml('original').includes('typed while loading'), 'pending edits not lost');
                const editor = tinyInstances.get(input.id);
                if (window.tinyMode === 'delay') {
                    assert(editor.getContent().includes('typed while loading'), 'ready editor uses newest fallback content');
                } else {
                    assert(!input.hidden && input.style.display !== 'none', 'failed or timed-out editor restores native source');
                    assert(!editor, 'failed editor cleaned up');
                    edit(input, '<p>after failure</p>');
                    assert(api.getHtml('original').includes('after failure'), 'fallback remains editable');
                }
                assert(tinyOptions.length === 2, 'no retry loop');
                ''', tiny=True, mode=mode)

    def test_validation_image_paste_drop_and_late_library_upgrade(self):
        self.run_checks(r'''
        assert(!api.validate(form), 'empty native body rejected without TinyMCE');
        edit(heading(cards()[0]), 'heading only');
        assert(!api.validate(form), 'heading only rejected');
        let received = [];
        api.configureImages('original', files => {
            received.push(...files);
            files.forEach(file => api.insertMarker('original', '【画像:' + file.name + '】'));
        });
        const paste = new Event('paste', {cancelable: true});
        Object.defineProperty(paste, 'clipboardData', {value: {items: [{kind:'file', getAsFile: () => new File(['x'], 'image.png', {type:'image/png'})}]}});
        body(cards()[0]).dispatchEvent(paste);
        assert(paste.defaultPrevented && received.length === 1, 'native paste uploaded');
        assert(received[0].name.startsWith('clipboard_') && api.hasContent('original'), 'paste marker counts as content');
        const drop = new Event('drop', {cancelable: true});
        Object.defineProperty(drop, 'dataTransfer', {value: {files: [new File(['x'], 'image.jpg', {type:'image/jpeg'})]}});
        body(cards()[0]).dispatchEvent(drop);
        assert(drop.defaultPrevented && received.length === 2, 'native drop uploaded');
        const otherBody = document.querySelector('[data-knowledge-editor="other"] textarea');
        edit(document.querySelector('[data-knowledge-editor="other"] input'), 'Other heading');
        edit(otherBody, '<p>second required field</p>');
        assert(api.validate(form), 'image markers pass validation');
        STUB
        api.upgrade(); await sleep(30);
        const editor = tinyInstances.get(body(cards()[0]).id);
        assert(editor.getContent().includes(received[0].name), 'late library upgrade preserves native edits');
        editor.emit('paste', paste);
        assert(received.length === 3, 'rich editor paste routes to same owner upload');
        editor.edit('<p>last typed text</p>');
        assert(api.sync('original').includes('last typed text'), 'typing immediately before sync retained');
        '''.replace('STUB', TINY_STUB))

    def test_fallback_safety_and_plaintext_conversion(self):
        self.run_checks(r'''
        const input = body(cards()[0]);
        assert(input.value.includes('<strong>bold</strong>') && input.value.includes('&lt;not html'), 'safe plain and minimal BBCode editing conversion');
        assert(api.getHtml('original') === initial, 'conversion not saved without edits');
        edit(input, '<p onclick="evil()">safe</p><scr' + 'ipt>evil()</scr' + 'ipt><a href="javascript:evil()">link</a><img src="/safe.png">');
        const result = api.getHtml('original');
        assert(!result.includes('onclick') && !result.includes('<script') && !result.includes('javascript:'), 'edited fallback strips active content');
        assert(result.includes('/safe.png'), 'safe image retained');
        ''', original='[b]bold[/b]\n<not html & text')


if __name__ == '__main__':
    unittest.main()