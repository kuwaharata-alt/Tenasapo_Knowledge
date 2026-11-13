"""Real FAQ/Tips form, persistence, renderer and offline browser integration."""

import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import FAQCategory, KnowledgeArticle, TipsArticle
from .templatetags.article_extras import render_inline_images


SECTION = ('<div class="knowledge-content-section"><h3>概要 &lt;任意&gt;</h3>'
           '<div class="knowledge-section-body"><p><strong>本文</strong></p>'
           '<ol><li>操作1</li><li><em>操作2</em></li></ol></div></div>'
           '<div class="knowledge-content-section"><h3>手順</h3>'
           '<div class="knowledge-section-body"><ul><li>確認</li></ul></div></div>')
SPECS = ((KnowledgeArticle, 'article', ('question', 'answer'), 'faq'),
         (TipsArticle, 'tip', ('body',), 'tips'))


class KnowledgeBlockFormFixture:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='block-form-admin', email='blocks@example.test', password='test-password',
        )
        cls.category = FAQCategory.objects.create(parent_name='PC', middle_name='設定')

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        return {'title': 'ブロック保存確認', 'registered_category': [self.category.pk],
                'question': SECTION, 'answer': SECTION, 'body': SECTION,
                'target_os_name': 'Windows PC', 'target_os_version': '11',
                'visible_to_systena': 'on', 'reference_links': '[]', **overrides}


class KnowledgeBlockFormTests(KnowledgeBlockFormFixture, TestCase):
    def test_create_edit_markup_keeps_original_serialization_and_shared_assets(self):
        for model, prefix, fields, kind in SPECS:
            item = model.objects.create(title='既存', body=SECTION, created_by=self.user)
            for url in (reverse(prefix + '_create'), reverse(prefix + '_edit', args=[item.pk])):
                with self.subTest(kind=kind, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, 'includes/knowledge_block_editor_assets.html')
                    source = response.content.decode()
                    self.assertEqual(source.count('const records = new Map(), owners = new Map()'), 1)
                    for field in fields:
                        self.assertRegex(source, rf'<textarea[^>]*name="{field}"[^>]*id="id_{field}"[^>]*data-wysiwyg="true"')
                    self.assertIn("toolbar: 'undo redo | bold italic | bullist numlist'", source)
                    self.assertNotIn('styleselect', source)
                    self.assertNotIn(' outdent', source)
                    self.assertIn('window.KnowledgeBlockEditors.configureImages(', source)
                    self.assertIn('window.KnowledgeBlockEditors.syncForm(', source)
                    self.assertIn('window.KnowledgeBlockEditors.validate(', source)

    def test_save_reload_and_render_sections_on_lists_details_and_preview(self):
        for model, prefix, fields, kind in SPECS:
            with self.subTest(kind=kind):
                response = self.client.post(reverse(prefix + '_create'), self.payload())
                self.assertEqual(response.status_code, 302)
                item = model.objects.get(title='ブロック保存確認')
                self.assertEqual(item.body, SECTION)
                if kind == 'faq':
                    self.assertEqual(item.summary, SECTION)
                response = self.client.post(reverse(prefix + '_edit', args=[item.pk]), self.payload())
                self.assertEqual(response.status_code, 302)
                item.refresh_from_db()
                self.assertEqual(item.body, SECTION)
                model.objects.filter(pk=item.pk).update(is_published=True, is_approved=True, expires_on=None)
                for url in (reverse(prefix + '_list'),
                            reverse(prefix + '_detail', args=[item.management_code])):
                    rendered = self.client.get(url)
                    self.assertContains(rendered, '<h3>概要 &lt;任意&gt;</h3>', html=True)
                    self.assertContains(rendered, 'class="knowledge-section-body"')
                    self.assertContains(rendered, '.knowledge-content-section > h3')
                    self.assertContains(rendered, '<ol><li>操作1</li><li><em>操作2</em></li></ol>', html=True)
                preview = self.client.post(reverse('preview_render'),
                                           json.dumps({'type': kind, **{key: SECTION for key in fields}}),
                                           content_type='application/json')
                self.assertEqual(preview.status_code, 200)
                self.assertTrue(preview.json()['ok'])
                for key in fields:
                    self.assertIn('<h3>手順</h3>', preview.json()[key + '_html'])

    def test_renderer_keeps_wrappers_and_legacy_styles_but_strips_active_html(self):
        source = SECTION + '<p style="color:red;text-indent:2em" onclick="evil()">旧本文</p><img src="javascript:evil()" onerror="evil()">'
        rendered = str(render_inline_images(source, []))
        self.assertIn('class="knowledge-content-section"', rendered)
        self.assertIn('class="knowledge-section-body"', rendered)
        self.assertIn('<h3>概要 &lt;任意&gt;</h3>', rendered)
        self.assertIn('color:red;', rendered)
        self.assertIn('text-indent:2em;', rendered)
        self.assertNotIn('onclick', rendered)
        self.assertNotIn('onerror', rendered)
        self.assertNotIn('javascript:', rendered)

    def test_missing_or_whitespace_block_headings_cannot_create_or_update(self):
        invalid_headings = ('', '<h3></h3>', '<h3> \t\n　&nbsp;&#8203;&#65279;</h3>',
                            '<h3><span>　</span></h3>', '<h3><script>not a heading</script></h3>')
        for model, prefix, fields, kind in SPECS:
            item = model.objects.create(title='変更しない', body=SECTION, created_by=self.user)
            original = model.objects.filter(pk=item.pk).values().get()
            for field in fields:
                for heading in invalid_headings:
                    # Check every section, not just the first one; a body h3 is not a heading.
                    invalid = SECTION + ('<div class="knowledge-content-section">' + heading +
                                         '<div class="knowledge-section-body"><h3>本文内の見出し</h3>'
                                         '<p>本文あり</p></div></div>')
                    for route, args in ((prefix + '_create', []), (prefix + '_edit', [item.pk])):
                        with self.subTest(kind=kind, field=field, heading=heading, route=route):
                            count = model.objects.count()
                            response = self.client.post(reverse(route, args=args), self.payload(**{field: invalid}))
                            self.assertEqual(response.status_code, 200)
                            form = response.context['form']
                            self.assertEqual(form.errors.as_data()[field][0].code, 'required_heading')
                            self.assertEqual(form[field].value(), invalid)
                            self.assertEqual(model.objects.count(), count)
                            self.assertEqual(model.objects.filter(pk=item.pk).values().get(), original)

    def test_legacy_html_plaintext_and_bbcode_save_without_heading_or_rewriting(self):
        for model, prefix, fields, kind in SPECS:
            for legacy in ('<p style="color:red">旧本文 &amp; 内容</p>\n<p>末尾</p>',
                           '旧本文\n二行目 < & >', '[b]旧本文[/b]\n[color=red]保持[/color]',
                           '<div class="knowledge-content-section extra">旧本文</div>',
                           '&lt;div class="knowledge-content-section"&gt;文字列&lt;/div&gt;'):
                with self.subTest(kind=kind, legacy=legacy):
                    payload = self.payload(**{field: legacy for field in fields})
                    response = self.client.post(reverse(prefix + '_create'), payload)
                    self.assertEqual(response.status_code, 302)
                    item = model.objects.latest('pk')
                    self.assertEqual(item.body, legacy)
                    response = self.client.post(reverse(prefix + '_edit', args=[item.pk]), payload)
                    self.assertEqual(response.status_code, 302)
                    item.refresh_from_db()
                    self.assertEqual(item.body, legacy)
                    if kind == 'faq':
                        self.assertEqual(item.summary, legacy)


class KnowledgeBlockFormBrowserTests(KnowledgeBlockFormFixture, TestCase):
    """Use actual Django-rendered pages; only CDN/network/Bootstrap are boundaries."""

    def run_page(self, source, fields, kind, *, editing=False):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((p for p in candidates if p and os.path.isfile(p)), None)
        if not browser:
            self.skipTest('Chrome/Edge is required')
        # No external scripts/styles are loaded, guaranteeing the native fallback.
        source = re.sub(r'<script\b[^>]*\bsrc=[^>]*>\s*</script>', '', source, flags=re.I)
        source = re.sub(r'<link\b[^>]*>', '', source, flags=re.I)
        setup = r'''<script>
window.browserErrors = []; window.requests = []; window.saved = [];
window.addEventListener('error', event => browserErrors.push(event.message));
window.addEventListener('unhandledrejection', event => browserErrors.push(String(event.reason)));
const modals = new WeakMap();
window.bootstrap = {Modal: {getOrCreateInstance(element) {
 if (!modals.has(element)) modals.set(element, {
  show() {element.classList.add('show'); element.dispatchEvent(new Event('shown.bs.modal'));},
  hide() {element.classList.remove('show'); setTimeout(() => element.dispatchEvent(new Event('hidden.bs.modal')), 5);}
 });
 return modals.get(element);
}}};
window.fetch = (url, options = {}) => {
 requests.push({url: String(url), options});
 const payload = options.body ? JSON.parse(options.body) : null;
 return Promise.resolve({ok: true, json: () => Promise.resolve(payload ? {
  ok: true, question_html: payload.question, answer_html: payload.answer, body_html: payload.body
 } : {categories: ['PC/設定'], results: [], total: 0, page: 1, pages: 1})});
};
</script>'''
        source = source.replace('<head>', '<head>' + setup, 1)
        harness = r'''<pre id="block-result">PENDING</pre><script>
document.addEventListener('DOMContentLoaded', async () => {
 const fields = FIELDS, kind = KIND, editing = EDITING;
 const form = document.querySelector('form[data-knowledge-completion]');
 const api = window.KnowledgeBlockEditors;
 const assert = (ok, message) => {if (!ok) throw new Error(message);};
 const edit = (node, value) => {node.value = value; node.dispatchEvent(new Event('input', {bubbles:true}));};
 const wait = () => new Promise(resolve => setTimeout(resolve, 70));
 try {
  assert(browserErrors.length === 0, browserErrors.join(';'));
  assert(!window.tinymce, 'CDN failure must use native fallback');
  const title = form.elements.namedItem('title'); edit(title, 'ブラウザ登録');
    const osName = form.elements.namedItem('target_os_name');
    edit(osName, 'Windows PC'); osName.dispatchEvent(new Event('change', {bubbles:true}));
  if (!form.querySelector('input[type="hidden"][name="registered_category"]')) {
   const category = document.createElement('input'); category.type = 'hidden';
   category.name = 'registered_category'; category.value = 'CATEGORY'; form.append(category);
  }
  const sources = fields.map(name => form.elements.namedItem(name));
  const originals = sources.map(source => source.value);
  const roots = sources.map(source => document.querySelector(`[data-knowledge-editor="${source.id}"]`));
  assert(roots.every(Boolean), 'all real form sources upgraded');
    if (!editing) {
     edit(roots[0].querySelector('input'), '見出しのみ');
     form.requestSubmit();
     assert(requests.length === 0, 'heading-only input cannot preview or pass duplicate gate');
     assert(roots[0].querySelector('[role="alert"]')?.hidden === false, 'body validation explained');
    }
  if (editing) {
   api.syncForm(form);
   sources.forEach((source, i) => assert(source.value === originals[i], 'untouched legacy exact'));
    assert(api.validate(form), 'legacy heading suggestions allow unchanged submission');
    sources.forEach((source, i) => assert(source.value === originals[i], 'validation never rewrites legacy'));
  }
  // Use the real submit listener: missing headings must not reach duplicate checking or preview.
  const firstHeading = roots[0].querySelector('input');
  assert(firstHeading.getAttribute('aria-required') === 'true', 'heading exposed as required');
  assert(!roots[0].querySelector('label').textContent.includes('任意'), 'no optional heading label');
  edit(roots[0].querySelector('textarea'), '<p>見出し未入力の本文</p>');
  for (const value of ['', ' \t　\u200b\ufeff ']) {
    edit(firstHeading, value);
    const before = requests.length;
    form.requestSubmit();
    assert(requests.length === before && saved.length === 0, 'missing heading blocks preview and save');
    assert(document.activeElement === firstHeading, 'focus missing heading');
    assert(firstHeading.getAttribute('aria-invalid') === 'true', 'invalid heading accessible');
    assert(roots[0].querySelector('[role="alert"]').textContent.includes('見出しを入力'), 'heading error explained');
  }
  roots.forEach((root, index) => {
   const heading = root.querySelector('input');
   const body = root.querySelector('textarea');
   edit(heading, '概要 <任意>'); edit(body, '<p><strong>本文' + index + '</strong></p>');
   root.querySelector(':scope > button').click();
   const second = root.querySelectorAll('.knowledge-block-card')[1];
   edit(second.querySelector('input'), '手順');
   edit(second.querySelector('textarea'), '<ol><li>操作1</li><li><em>操作2</em></li></ol>');
  });
  await wait();
  fields.forEach(name => {
   const icon = document.querySelector(`[href="#form-section-${name}"] .form-completion-icon`);
   assert(icon?.dataset.state === 'complete', name + ' completion observes block edits');
  });
  // Paste uses the real form's callback and real file input, not a mock uploader.
  const target = roots[0].querySelector('textarea'); target.focus();
  const paste = new Event('paste', {cancelable:true});
  Object.defineProperty(paste, 'clipboardData', {value:{items:[{
   kind:'file', getAsFile:() => new File(['image'], 'pasted.png', {type:'image/png'})
  }]}});
  target.dispatchEvent(paste);
  assert(paste.defaultPrevented, 'paste routed to form image attachments');
  const upload = form.elements.namedItem(kind === 'faq' ? 'question_images' : 'tips_images');
  assert(upload.files.length === 1, 'pasted image attached to real field');
  assert(sources[0].value.includes('【画像:'), 'image marker serialized to original source');
  const references = document.getElementById('reference_links_container');
    document.getElementById('add_reference_link_button').click();
  const reference = references.querySelector('input[type="url"]');
    assert(reference, 'reference link control retained');
    edit(reference, 'https://example.test/guide');
    const files = new DataTransfer(); files.items.add(new File(['attachment'], 'guide.txt', {type:'text/plain'}));
    form.elements.namedItem('file_attachments').files = files.files;
  form.addEventListener('submit', event => {
   if (!event.defaultPrevented) { event.preventDefault(); saved.push(Object.fromEntries(new FormData(form))); }
  });
    assert(form.checkValidity(), 'native form fields valid: ' + [...form.elements].filter(n => n.validity && !n.validity.valid).map(n => n.name).join(','));
  form.requestSubmit(); await wait();
  if (!editing) {
   const duplicate = document.getElementById('knowledgeDuplicateModal');
   assert(duplicate.classList.contains('show'), 'duplicate gate before preview');
   assert(saved.length === 0 && !requests.some(r => r.options.method === 'POST'), 'gate cannot save or preview');
   document.getElementById('knowledgeDuplicateContinue').click(); await wait();
  }
  const preview = document.getElementById(kind + 'PreviewModal');
  assert(preview.classList.contains('show'), 'actual preview opened');
  const request = requests.find(r => r.options.method === 'POST');
  assert(request, 'preview request was sent');
  const payload = JSON.parse(request.options.body);
  fields.forEach(name => assert(payload[name] === form.elements.namedItem(name).value, 'preview source synchronized'));
  const rendered = preview.querySelector('.knowledge-content-section');
  assert(rendered?.querySelector('h3').textContent === '概要 <任意>', 'safe heading displayed');
    const headingStyle = getComputedStyle(rendered.querySelector('h3'));
    assert(headingStyle.backgroundColor === 'rgba(0, 0, 0, 0)' && headingStyle.backgroundImage === 'none', 'no heading background fill');
        assert(headingStyle.borderBottomWidth === '4px' && headingStyle.borderBottomStyle === 'solid', 'thick underline');
        assert(headingStyle.borderLeftWidth === '0px' && headingStyle.borderLeftStyle === 'none', 'no vertical line');
        assert(headingStyle.textDecorationLine === 'none', 'no duplicate text underline');
        const heading = rendered.querySelector('h3');
        const em = parseFloat(headingStyle.fontSize);
        assert(parseFloat(headingStyle.paddingLeft) === em && parseFloat(headingStyle.paddingRight) === em, 'one fullwidth character padding on each side');
        const textRange = document.createRange(); textRange.selectNodeContents(heading);
        assert(Math.abs(heading.getBoundingClientRect().width - textRange.getBoundingClientRect().width - 2 * em) < 2, 'underline fits text plus side padding');
  assert(preview.querySelector('ol li em'), 'numbered list and italic preview survive');
    assert(preview.querySelector('img[src^="blob:"]'), 'pasted image rendered from inline marker');
  const before = requests.length;
  document.getElementById(kind + 'PreviewConfirmBtn').click(); await wait();
  assert(saved.length === 1 && requests.length === before, 'confirm saves once without reopening gate');
  fields.forEach(name => assert(saved[0][name] === form.elements.namedItem(name).value, 'submitted original source exact'));
    assert(saved[0].reference_links.includes('https://example.test/guide'), 'reference links synchronized');
    assert(saved[0].file_attachments.name === 'guide.txt', 'attachment remains in final POST');
  assert(!Object.keys(saved[0]).some(name => name.startsWith('knowledge-block-')), 'no internal fields posted');
  assert(browserErrors.length === 0, browserErrors.join(';'));
  document.getElementById('block-result').textContent = 'PASS';
 } catch (error) {document.getElementById('block-result').textContent = 'FAIL: ' + (error.stack || error.message);}
});
</script>'''.replace('FIELDS', json.dumps(fields)).replace('KIND', json.dumps(kind)).replace(
            'EDITING', 'true' if editing else 'false').replace('CATEGORY', str(self.category.pk))
        with tempfile.TemporaryDirectory(prefix='knowledge-real-form-') as directory:
            page = Path(directory) / 'form.html'
            page.write_text(source.replace('</body>', harness + '</body>'), encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=5000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=45, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            match = re.search(r'<pre id="block-result">(.*?)</pre>', result.stdout, re.S)
            self.assertIsNotNone(match, result.stderr[-2000:])
            self.assertEqual(html.unescape(match.group(1)), 'PASS')

    def test_real_create_forms_fallback_blocks_images_completion_preview_and_submit(self):
        for _, prefix, fields, kind in SPECS:
            with self.subTest(kind=kind):
                response = self.client.get(reverse(prefix + '_create'))
                self.assertEqual(response.status_code, 200)
                self.run_page(response.content.decode(), fields, kind)

    def test_real_edit_forms_preserve_legacy_until_edit_and_submit_without_duplicate_gate(self):
        for model, prefix, fields, kind in SPECS:
            with self.subTest(kind=kind):
                item = model.objects.create(title='旧記事', body='<p style="color:red">旧本文</p>',
                                            created_by=self.user, category=self.category.full_name)
                response = self.client.get(reverse(prefix + '_edit', args=[item.pk]))
                self.assertEqual(response.status_code, 200)
                self.run_page(response.content.decode(), fields, kind, editing=True)