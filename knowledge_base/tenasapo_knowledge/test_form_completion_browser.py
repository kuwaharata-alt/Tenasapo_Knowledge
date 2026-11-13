"""Exercise the actual FAQ/Tips completion and navigation assets in Chrome.

Only browser scheduling and TinyMCE's external API are controlled by the
fixture; completion decisions, DOM events, validity and layout are production
JavaScript/browser behavior. No Django server or database is needed.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FormCompletionBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        candidates = [
            shutil.which("chromium"),
            shutil.which("google-chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        cls.browser = next(
            (path for path in candidates if path and os.path.isfile(path)), None
        )
        if cls.browser is None:
            raise unittest.SkipTest(
                "Chrome/Edge/Chromium is required for JavaScript regression tests"
            )
        includes = Path(__file__).resolve().parent.parent / "templates/includes"
        cls.assets = (
            (includes / "knowledge_form_navigation_assets.html").read_text(encoding="utf-8")
            + (includes / "knowledge_form_completion_assets.html").read_text(encoding="utf-8")
        )

    def _fixture(self, kind, opted_in):
        rich_fields = ("question", "answer") if kind == "faq" else ("body",)
        middle = rich_fields + (("pdf",) if kind == "tips" else ())
        sections = ("title", "category", "os", *middle, "reference", "visibility")
        links = "".join(
            f'<a href="#form-section-{key}">{key}</a>' for key in sections
        )
        rich_columns = "".join(
            f'<div class="col-12 rich-column" data-column="{key}">'
            f'<label id="form-section-{key}" data-form-section tabindex="-1" '
            f'for="id_{key}">{key}</label>'
            f'<textarea id="id_{key}" name="{key}" required></textarea></div>'
            for key in rich_fields
        )
        pdf_column = """
<div class="col-12 pdf-column" data-column="pdf">
  <div id="form-section-pdf" data-form-section tabindex="-1">PDF</div>
  <div id="existing-pdf"></div>
  <input type="file" id="id_pdf_file" name="pdf_file" accept=".pdf">
</div>
""" if kind == "tips" else ""
        form_id = "articleMainForm" if kind == "faq" else "tipsMainForm"
        prefix = "faq" if kind == "faq" else "tips"
        opt_in = f' data-knowledge-completion="{kind}"' if opted_in else ""
        return """<!doctype html><html><head><meta charset="utf-8">
<style>
html { font-size: 16px; }
body { margin: 0; }
#app-main { position: absolute; top: 50px; height: 320px; width: 1180px; overflow: auto; }
.knowledge-form-layout { display: flex; }
.knowledge-form-nav-column { width: 240px; flex-shrink: 0; }
.fields { width: 800px; }
.card-body > .row > div { box-sizing: border-box; margin-top: 0 !important; }
.title-column { height: 100px; }
.heading-column { height: 40px; }
.category-column { height: 640px; }
.os-column { height: 150px; }
.rich-column { height: 480px; }
.pdf-column { height: 160px; }
.reference-column { height: 180px; }
.visibility-column { height: 140px; }
[data-form-section] { display: block; height: 32px; }
.scroll-tail { height: 400px; }
#result { position: absolute; top: 800px; }
</style></head><body>
<main id="app-main"><div class="knowledge-form-layout">
<aside class="knowledge-form-nav-column"><nav class="knowledge-form-nav">
<div class="knowledge-form-nav-links">""" + links + """</div></nav></aside>
<div class="fields"><form id=""" + f'"{form_id}"{opt_in}' + """>
<div class="card-body"><div class="row">
<div class="col-12 title-column" data-column="title">
  <label id="form-section-title" data-form-section tabindex="-1" for="id_title">Title</label>
  <input id="id_title" name="title" required>
</div>
<div class="col-12 heading-column">
  <div id="form-section-category" data-form-section tabindex="-1">Category</div>
</div>
<div class="col-12 category-column" data-column="category">
  <div class="category-browser"><div id=""" + f'"{prefix}CategorySub2Pane"' + """>
    <label><input type="checkbox" value="17">Visible category choice</label>
  </div></div>
  <div id=""" + f'"{prefix}CategoryHiddenContainer"' + """ style="display:none"></div>
</div>
<div class="col-12" hidden><input name="tags" value=""></div>
<div class="col-12 heading-column">
  <div id="form-section-os" data-form-section tabindex="-1">OS</div>
</div>
<div class="col-12 os-column" data-column="os">
  <div data-target-os-row="primary">
    <select id="id_target_os_name" name="target_os_name" required>
      <option value="">Choose OS</option><option value="Windows PC">Windows PC</option>
    </select>
    <select id="id_target_os_version" name="target_os_version"><option value="">Any version</option></select>
    <select id="id_target_os_condition" name="target_os_condition"><option value="">Any condition</option></select>
  </div>
  <input type="hidden" name="target_os_entries" value="[]">
</div>
<input type="hidden" name="source_published_at"><input type="hidden" name="expires_on">
""" + rich_columns + pdf_column + """
<div class="col-12 reference-column" data-column="reference">
  <div id="form-section-reference" data-form-section tabindex="-1">Reference</div>
  <div id="reference_links_container"><div><input id="reference-url" type="url"></div></div>
  <input type="hidden" name="reference_links" value="[]">
  <input id="id_file_attachments" name="file_attachments" type="file" multiple>
  <div id="existing-attachments"></div>
</div>
<div class="col-12 visibility-column" data-column="visibility">
  <div id="form-section-visibility" data-form-section tabindex="-1">Visibility</div>
  <label><input id="id_visible_to_customer" type="checkbox" name="visible_to_customer">Customer</label>
  <label><input id="id_visible_to_systena" type="checkbox" name="visible_to_systena">Internal</label>
</div>
<input type="hidden" name="submit_action" value="create">
</div></div></form><div class="scroll-tail"></div></div></div></main>
<button id="outside" type="button">Outside form</button>
<pre id="result">PENDING</pre>
<script>
// Drain production animation callbacks explicitly, retaining real browser layout.
window.completionFrames = [];
window.requestAnimationFrame = callback => window.completionFrames.push(callback);
</script>
"""

    def _run_browser(self, kind, checks, *, opted_in=True):
        harness = r"""<script>
document.addEventListener('DOMContentLoaded', async () => {
    const root = document.getElementById('app-main');
    const form = document.querySelector('form');
    const kind = form.id === 'articleMainForm' ? 'faq' : 'tips';
    const richFields = kind === 'faq' ? ['question', 'answer'] : ['body'];
    const required = ['title', 'category', 'os', ...richFields];
    const optional = kind === 'faq' ? ['reference', 'visibility'] : ['pdf', 'reference', 'visibility'];
    const link = key => document.querySelector(`a[href="#form-section-${key}"]`);
    const field = name => form.elements.namedItem(name);
    const assert = (value, message) => { if (!value) throw new Error(message); };
    const state = (key, expected, context = '') => {
        const icons = link(key).querySelectorAll('span.form-completion-icon');
        assert(icons.length === 1, `${key}: expected one span icon; ${context}`);
        const icon = icons[0];
        assert(icon.dataset.state === expected,
            `${key}: expected ${expected}, got ${icon.dataset.state}; ${context}`);
        assert(icon.getAttribute('role') === 'img', `${key}: accessible image role`);
        assert(Boolean(icon.getAttribute('aria-label')?.trim()), `${key}: accessible state label`);
    };
    const settle = async () => {
        // Yield for native scroll/MutationObserver/ResizeObserver delivery, then
        // drain both assets' RAF queues. No arbitrary wall-clock sleeps.
        for (let tick = 0; tick < 3; tick++) {
            await new Promise(resolve => setTimeout(resolve, 0));
            let count = 0;
            while (window.completionFrames.length) {
                assert(++count < 100, 'animation callbacks did not settle');
                window.completionFrames.shift()(performance.now());
            }
        }
    };
    const change = async (control, value, type = 'input') => {
        control.value = value;
        control.dispatchEvent(new Event(type, {bubbles: true}));
        await settle();
    };
    const focus = async control => {
        control.focus({preventScroll: true});
        assert(document.activeElement === control, 'fixture control must receive real focus');
        await settle();
    };
    const jump = async key => {
        link(key).click();
        await settle();
    };
    const scrollToTop = async top => {
        root.scrollTop = top;
        root.dispatchEvent(new Event('scroll'));
        await settle();
    };
    const contentTop = node => root.scrollTop + node.getBoundingClientRect().top
        - root.getBoundingClientRect().top - root.clientTop;
    const files = async (control, names) => {
        const transfer = new DataTransfer();
        names.forEach(name => transfer.items.add(new File(['fixture'], name)));
        control.files = transfer.files;
        control.dispatchEvent(new Event('change', {bubbles: true}));
        await settle();
    };
    const editorState = async (id, focused = false) => {
        document.dispatchEvent(new CustomEvent('knowledge-editor-state', {detail: {id, focused}}));
        await settle();
    };
    try {
        await settle();
""" + checks + r"""
        document.getElementById('result').textContent = 'PASS: completion checks';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
    }
});
</script></body></html>"""
        with tempfile.TemporaryDirectory(prefix="nexus-completion-") as directory:
            page = Path(directory) / "completion.html"
            page.write_text(
                self._fixture(kind, opted_in) + self.assets + harness, encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    self.browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-first-run",
                    "--disable-background-networking",
                    "--run-all-compositor-stages-before-draw",
                    "--force-prefers-reduced-motion",
                    "--window-size=1440,1000",
                    f"--user-data-dir={directory}/profile",
                    "--virtual-time-budget=5000",
                    "--dump-dom",
                    page.as_uri(),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            start = completed.stdout.find('<pre id="result">')
            end = completed.stdout.find("</pre>", start)
            result = completed.stdout[start:end] if start >= 0 and end >= 0 else ""
            self.assertIn(
                '<pre id="result">PASS:', result, result or completed.stderr[-2000:]
            )

    def _check_both(self, checks, *, opted_in=True):
        for kind in ("faq", "tips"):
            with self.subTest(kind=kind):
                self._run_browser(kind, checks, opted_in=opted_in)

    def test_initial_and_resize_are_neutral_with_accessible_icons(self):
        self._check_both(r"""
        assert(document.querySelectorAll('.form-completion-icon').length
            === required.length + optional.length, 'one icon for each actual section');
        [...required, ...optional].forEach(key => state(key, 'empty', 'initial load'));
        root.style.height = '280px';
        window.dispatchEvent(new Event('resize'));
        await settle();
        [...required, ...optional].forEach(key => state(key, 'empty', 'resize is not a visit'));
        """)

    def test_focus_movement_warns_skipped_required_sections(self):
        self._check_both(r"""
        await focus(field('title'));
        state('title', 'empty', 'currently focused blank control stays neutral');
        await focus(field(richFields[0]));
        ['title', 'category', 'os'].forEach(key => state(key, 'warning', 'skipped by focus'));
        state(richFields[0], 'empty', 'destination is not yet skipped');
        await focus(document.getElementById('outside'));
        state(richFields[0], 'warning', 'leaving the form visits the current field');
        optional.forEach(key => state(key, 'empty'));
        """)

    def test_navigation_jump_warns_all_skipped_required_not_optional(self):
        self._check_both(r"""
        await jump('visibility');
        required.forEach(key => state(key, 'warning', 'navigation jumped past required section'));
        optional.forEach(key => state(key, 'empty', 'untouched optional section is neutral'));
        """)

    def test_active_title_updates_on_input_and_whitespace_is_missing(self):
        self._check_both(r"""
        await focus(field('title'));
        await change(field('title'), 'A useful title');
        state('title', 'complete', 'input updates before blur');
        await change(field('title'), ' \t\n\u00a0 ');
        state('title', 'empty', 'whitespace is not complete even while focused');
        await focus(field('target_os_name'));
        state('title', 'warning', 'blank title warns after leaving');
        await change(field('title'), 'Corrected title', 'change');
        state('title', 'complete', 'change event also clears a previous warning');
        """)

    def test_category_uses_hidden_registered_values_across_pane_mutations(self):
        self._check_both(r"""
        const prefix = kind === 'faq' ? 'faq' : 'tips';
        const pane = document.getElementById(`${prefix}CategorySub2Pane`);
        const hidden = document.getElementById(`${prefix}CategoryHiddenContainer`);
        const choice = pane.querySelector('input');
        choice.checked = true;
        choice.dispatchEvent(new Event('change', {bubbles: true}));
        await settle();
        state('category', 'empty', 'visible checkbox alone is not submitted category state');
        hidden.innerHTML = '<input type="hidden" name="registered_category" value="">';
        await settle();
        state('category', 'empty', 'empty hidden value is not a selection');
        hidden.insertAdjacentHTML('beforeend',
            '<input type="hidden" name="registered_category" value="17">');
        await settle();
        state('category', 'complete', 'childList mutation without an input event');
        pane.innerHTML = '<div>Another category pane, with no checked visible choice</div>';
        await settle();
        assert(!hidden.getClientRects().length, 'registered controls really are hidden');
        state('category', 'complete', 'selection survives switching visible panes');
        await focus(field('target_os_name'));
        hidden.replaceChildren();
        await settle();
        state('category', 'warning', 'removing submitted selection updates by mutation');
        """)

    def test_os_requires_name_but_not_version_or_condition(self):
        self._check_both(r"""
        await focus(field(richFields[0]));
        state('os', 'warning', 'missing required OS');
        await change(field('target_os_name'), 'Windows PC', 'change');
        assert(field('target_os_version').value === '', 'version fixture is blank');
        assert(field('target_os_condition').value === '', 'condition fixture is blank');
        state('os', 'complete', 'OS name suffices without extra required fields');
        await change(field('target_os_name'), '', 'change');
        state('os', 'warning', 'clearing selected OS restores warning');
        """)

    def test_rich_text_fallback_rejects_formatting_and_nbsp_only_html(self):
        self._check_both(r"""
        assert(!window.tinymce, 'this scenario must use DOMParser fallback');
        for (const key of richFields) {
            await focus(field(key));
            for (const html of ['<p><br></p>', '<div><strong> &nbsp; </strong></div>',
                '<p>&#160;\u200b\ufeff</p>']) {
                await change(field(key), html);
                assert(link(key).querySelector('.form-completion-icon').dataset.state !== 'complete',
                    `${key}: formatting/whitespace must not count as text: ${html}`);
            }
            await focus(document.getElementById('outside'));
            state(key, 'warning', 'empty rich text warns after focus leaves');
            await change(field(key), '<p><strong>Actual &amp; useful content</strong></p>');
            state(key, 'complete', 'HTML text content is present');
            await change(field(key), '<p>&nbsp;<br></p>');
            state(key, 'warning', 'removing rich text restores warning');
        }
        """)

    def test_live_editor_contents_override_stale_textarea_and_report_focus(self):
        self._check_both(r"""
        const editors = new Map();
        window.tinymce = {get: id => editors.get(id) || null};
        for (const key of richFields) {
            const textarea = field(key);
            textarea.value = '<p>Stale saved content</p>';
            const editor = {
                id: textarea.id, initialized: true, text: '\u00a0 \u200b',
                getContent(options) {
                    assert(options?.format === 'text', 'read live editor text, not markup');
                    return this.text;
                },
            };
            editors.set(editor.id, editor);
        }
        await editorState(field(richFields[0]).id, true);
        ['title', 'category', 'os'].forEach(key => state(key, 'warning', 'editor focus visits prior fields'));
        for (const key of richFields) {
            const textarea = field(key);
            const editor = editors.get(textarea.id);
            assert(link(key).querySelector('.form-completion-icon').dataset.state !== 'complete',
                `${key}: live empty editor overrides stale nonempty textarea`);
            await focus(document.getElementById('outside'));
            textarea.value = '<p>&nbsp;</p>';
            editor.text = 'Unsaved live editor text';
            await editorState(editor.id, true);
            state(key, 'complete', 'custom editor event updates before textarea sync');
            assert(textarea.value === '<p>&nbsp;</p>', 'textarea deliberately remains stale');
            editor.text = '\u00a0\n\u200b';
            await editorState(editor.id);
            assert(link(key).querySelector('.form-completion-icon').dataset.state !== 'complete',
                `${key}: editor deletion updates immediately`);
            await focus(field('visible_to_customer'));
            state(key, 'warning', 'leaving live editor marks it visited');
            // An uninitialized editor must not mask textarea fallback content.
            editor.initialized = false;
            await change(textarea, '<p>Fallback while editor initializes</p>');
            state(key, 'complete', 'uninitialized editor uses textarea HTML');
        }
        """)

    def test_dynamic_url_rows_validate_before_completing_and_clear_to_neutral(self):
        self._check_both(r"""
        const container = document.getElementById('reference_links_container');
        const first = document.getElementById('reference-url');
        await focus(first);
        await change(first, 'not a URL');
        assert(!first.validity.valid, 'fixture URL really is invalid');
        assert(link('reference').querySelector('.form-completion-icon').dataset.state !== 'complete',
            'invalid nonblank URL must not turn green');
        await focus(field('visible_to_customer'));
        state('reference', 'warning', 'visited invalid optional URL needs attention');
        await change(first, 'https://example.test/reference');
        state('reference', 'complete', 'corrected URL clears warning');
        container.insertAdjacentHTML('beforeend', '<div><input type="url" value="broken"></div>');
        await settle();
        state('reference', 'warning', 'invalid dynamic row prevents partial green state');
        const second = container.lastElementChild.querySelector('input');
        await change(second, 'https://example.test/second', 'change');
        state('reference', 'complete', 'all populated URL rows are valid');
        container.replaceChildren();
        await settle();
        state('reference', 'empty', 'removing optional rows is neutral, never mandatory');
        """)

    def test_new_and_existing_attachments_update_reference_completion(self):
        self._check_both(r"""
        await focus(field('visible_to_customer'));
        state('reference', 'empty');
        const attachments = field('file_attachments');
        await files(attachments, ['notes.txt', 'diagram.png']);
        assert(attachments.files.length === 2, 'real browser FileList is populated');
        state('reference', 'complete', 'selected attachments count without URLs');
        await files(attachments, []);
        state('reference', 'empty', 'clearing optional attachments stays neutral');
        const existing = document.getElementById('existing-attachments');
        existing.innerHTML = '<div data-existing-attachment-name="saved.txt">saved.txt</div>';
        await settle();
        state('reference', 'complete', 'existing attachment is detected by childList mutation');
        existing.replaceChildren();
        await settle();
        state('reference', 'empty', 'removing existing attachment clears optional completion');
        """)

    def test_tips_pdf_file_existing_pdf_and_clear_switch(self):
        self._run_browser("tips", r"""
        await focus(field('visible_to_customer'));
        state('pdf', 'empty', 'blank PDF is optional even after passing it');
        await files(field('pdf_file'), ['guide.pdf']);
        state('pdf', 'complete', 'new PDF upload');
        await files(field('pdf_file'), []);
        state('pdf', 'empty', 'clearing new upload');
        document.getElementById('existing-pdf').innerHTML =
            '<a href="saved.pdf">Saved PDF</a>' +
            '<input type="checkbox" role="switch" id="id_clear_pdf" name="clear_pdf">';
        await settle();
        state('pdf', 'complete', 'existing PDF with unchecked clear switch');
        field('clear_pdf').checked = true;
        field('clear_pdf').dispatchEvent(new Event('change', {bubbles: true}));
        await settle();
        state('pdf', 'empty', 'PDF marked for deletion is not complete or mandatory');
        field('clear_pdf').checked = false;
        field('clear_pdf').dispatchEvent(new Event('change', {bubbles: true}));
        await settle();
        state('pdf', 'complete', 'undo deletion restores existing PDF');
        """)

    def test_visibility_is_optional_and_tracks_checked_boxes(self):
        self._check_both(r"""
        await focus(field('visible_to_customer'));
        await focus(document.getElementById('outside'));
        state('visibility', 'empty', 'unchecked visibility is not required');
        for (const name of ['visible_to_customer', 'visible_to_systena']) {
            field(name).checked = true;
            field(name).dispatchEvent(new Event('change', {bubbles: true}));
            await settle();
            state('visibility', 'complete', `${name} selection`);
            field(name).checked = false;
            field(name).dispatchEvent(new Event('change', {bubbles: true}));
            await settle();
            state('visibility', 'empty', 'all visibility boxes unchecked again');
        }
        """)

    def test_scroll_warns_only_after_whole_section_and_keeps_navigation_highlights(self):
        self._check_both(r"""
        const categoryHeading = document.getElementById('form-section-category');
        const categoryBody = document.querySelector('[data-column="category"]');
        assert(link('title').classList.contains('is-current'), 'initial title highlight');
        assert(link('category').classList.contains('is-current'), 'initial visible category highlight');
        await scrollToTop(contentTop(categoryHeading) + 180);
        state('title', 'warning', 'entire title column passed');
        state('category', 'empty', 'heading passed but oversized category body is still active');
        assert(link('category').classList.contains('is-current'), 'category body keeps current highlight');
        assert(link('category').classList.contains('is-partially-visible'), 'oversized section is partial');
        assert(!link('title').classList.contains('is-current'), 'passed title is not current');
        const osTop = contentTop(document.getElementById('form-section-os'));
        await scrollToTop(osTop - 100);
        state('category', 'empty', 'visible category tail has not been passed');
        assert(link('category').classList.contains('is-current'), 'category tail highlights');
        assert(link('os').classList.contains('is-current'), 'simultaneously visible OS highlights');
        assert(!link('os').classList.contains('is-partially-visible'), 'whole OS section fits viewport');
        assert(document.querySelectorAll('[aria-current="location"]').length === 1,
            'shared navigation retains one accessible current item');
        await scrollToTop(contentTop(categoryBody) + categoryBody.offsetHeight + 10);
        state('category', 'warning', 'whole category body passed, not only its heading');
        state('os', 'empty', 'scroll has not yet passed all OS columns');
        const rich = document.querySelector(`[data-column="${richFields[0]}"]`);
        await scrollToTop(contentTop(rich) + 150);
        state('os', 'warning', 'whole OS section passed despite hidden metadata');
        state(richFields[0], 'empty', 'within oversized editor column must not warn');
        await scrollToTop(contentTop(rich) + rich.offsetHeight + 10);
        state(richFields[0], 'warning', 'whole editor column passed');
        await scrollToTop(root.scrollHeight);
        optional.forEach(key => state(key, 'empty', 'optional sections passed by scroll never warn'));
        """)

    def test_form_without_opt_in_gets_no_completion_icons(self):
        self._check_both(r"""
        assert(!form.hasAttribute('data-knowledge-completion'), 'fixture is not opted in');
        assert(!document.querySelector('.form-completion-icon'), 'no initial icons on other forms');
        await focus(field('title'));
        await change(field('title'), 'Unrelated form title');
        await jump('visibility');
        document.getElementById('existing-attachments').innerHTML =
            '<div data-existing-attachment-name="unrelated.txt">unrelated.txt</div>';
        await scrollToTop(root.scrollHeight);
        await editorState(field(richFields[0]).id, true);
        assert(!document.querySelector('.form-completion-icon'), 'events cannot opt in an unrelated form');
        assert(!document.querySelector('.form-completion-legend'), 'no completion legend on other forms');
        """, opted_in=False)