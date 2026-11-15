"""Real form views/templates in offline Chrome; use test_knowledge_tabs_settings.

Only the existing category browser script is executed for FAQ/Tips (the editor
and CDN runtimes are deliberately excluded). Category/document scripts run as
rendered. CSP and resolver rules block Chrome networking; Python calls are guarded.
"""

import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import FAQCategory, KnowledgeArticle, ProjectDocument, TipsArticle
from .test_knowledge_tabs import NoNetwork, OfflineChrome


class CategoryGenreFormTests(NoNetwork, OfflineChrome, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username='Admin', email='offline@example.test', password='test')
        self.client.force_login(self.user)
        self.pc = FAQCategory.objects.create(parent_name='PC', middle_name='genre-direct')
        self.child = FAQCategory.objects.create(parent_name='サーバー', middle_name='genre-middle', child_name='genre-child')
        self.other = FAQCategory.objects.create(parent_name='未来の親', middle_name='genre-other')

    def browser(self, response, checks, *, multi=False):
        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        scripts = re.findall(r'<script\b[^>]*>(.*?)</script\s*>', rendered, re.S | re.I)
        helper = next(script for script in scripts if 'window.setupCategoryBrowserGenre = function' in script)
        if multi:
            production = next(script for script in scripts if '(function setupCategoryBrowser()' in script)
            production = production[production.index('(function setupCategoryBrowser()'):]
            production = production[:production.index('})();') + len('})();')]
            production = "document.addEventListener('DOMContentLoaded', function () {" + production + '});'
        else:
            production = next(script for script in scripts if 'const categoryBrowserData =' in script
                              or 'const isQrTab =' in script)
        source = re.sub(r'<script\b[^>]*>.*?</script\s*>', '', rendered, flags=re.S | re.I)
        source = re.sub(r'<link\b[^>]*>', '', source, flags=re.I)
        source = source.replace('<head>', '''<head><meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'">
<style>.d-flex {display:flex!important;}</style>''', 1)
        query = response.wsgi_request.GET.urlencode()
        harness = '<script>history.replaceState(null, "", ' + json.dumps('?' + query) + ');</script>'
        harness += '''<pre id="result">PENDING</pre><script>
const errors = [];
window.addEventListener('error', event => errors.push(event.message));
const assert = (ok, message) => {if (!ok) throw new Error(message);};
const change = element => element.dispatchEvent(new Event('change', {bubbles:true}));
const visible = pane => [...pane.querySelectorAll('[data-parent-name]')]
    .filter(row => getComputedStyle(row).display !== 'none').map(row => row.dataset.parentName);
</script><script>''' + helper + '</script><script>' + production + '''</script><script>
document.addEventListener('DOMContentLoaded', () => {
try {
assert(errors.length === 0, errors.join(';'));
''' + checks + '''
assert(errors.length === 0, errors.join(';'));
document.getElementById('result').textContent = 'PASS: genre forms';
} catch(error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
});</script>'''
        source = source.replace('</body>', harness + '</body>', 1)
        self.run_offline_page(source, expected='PASS: genre forms')

    def test_qa_and_tips_create_edit_and_error_multi_ids(self):
        for model, prefix, dom in ((KnowledgeArticle, 'article', 'faq'), (TipsArticle, 'tip', 'tips')):
            item = model.objects.create(title='既存', body='本文', created_by=self.user,
                                        category=self.child.full_name + ',' + self.other.full_name)
            for state in ('create', 'edit', 'create-error', 'edit-error'):
                with self.subTest(kind=prefix, state=state):
                    url = reverse(prefix + ('_edit' if state.startswith('edit') else '_create'),
                                  args=[item.pk] if state.startswith('edit') else None)
                    if 'error' in state:
                        response = self.client.post(url, {'title': '', 'registered_category': [self.child.pk, self.other.pk]})
                        self.assertIn('title', response.context['form'].errors)
                    else:
                        response = self.client.get(url)
                    initial_ids = [] if state == 'create' else [str(self.child.pk), str(self.other.pk)]
                    self.assertCountEqual(json.loads(response.context['registered_category_values_json']), initial_ids)
                    checks = '''
const selector = document.getElementById('DOMCategoryGenre');
const pane = document.getElementById('DOMCategoryParentPane');
const hidden = document.getElementById('DOMCategoryHiddenContainer');
const ids = () => [...hidden.querySelectorAll('input')].map(input => input.value).sort().join(',');
assert(ids() === INITIAL_IDS, 'initial IDs');
assert(selector.value === INITIAL_GENRE, 'initial/edit/error genre');
if (INITIAL_GENRE) assert(document.querySelector('[data-category-id="CHILD_ID"]').checked, 'initial child restored');
selector.value = 'client'; change(selector);
assert(visible(pane).includes('PC') && !visible(pane).includes('サーバー') && !visible(pane).includes('未来の親'), 'client parents');
pane.querySelector('[data-parent-name="PC"]').click();
document.querySelector('[data-category-id="PC_ID"]').click();
const saved = ids();
assert(saved.split(',').includes('PC_ID'), 'new selection hidden');
selector.value = 'infrastructure'; change(selector);
assert(visible(pane).includes('サーバー') && !visible(pane).includes('PC'), 'infrastructure parents');
assert(ids() === saved, 'cross genre IDs preserved');
pane.querySelector('[data-parent-name="サーバー"]').click();
document.querySelector('[data-sub1="genre-middle"]').click();
assert(document.querySelector('[data-category-id="CHILD_ID"]').checked === Boolean(INITIAL_GENRE), 'child checked restored');
selector.value = 'other'; change(selector);
assert(visible(pane).includes('未来の親') && !visible(pane).includes('PC'), 'unknown in other');
assert(ids() === saved, 'other IDs preserved');
selector.value = 'client'; change(selector);
pane.querySelector('[data-parent-name="PC"]').click();
assert(document.querySelector('[data-category-id="PC_ID"]').checked, 'return checked');
document.querySelector('[data-category-id="PC_ID"]').click();
assert(ids() === INITIAL_IDS, 'only explicit uncheck removes ID');
selector.value = ''; change(selector);
assert(visible(pane).includes('PC') && visible(pane).includes('サーバー') && visible(pane).includes('未来の親'), 'all parents');
'''.replace('DOM', dom).replace('INITIAL_IDS', json.dumps(','.join(sorted(initial_ids))))
                    checks = checks.replace('INITIAL_GENRE', json.dumps('' if state == 'create' else 'infrastructure'))
                    checks = checks.replace('CHILD_ID', str(self.child.pk)).replace('PC_ID', str(self.pc.pk))
                    self.browser(response, checks, multi=True)

    def test_category_management_create_edit_error_and_focus_preserve_crud(self):
        for state in ('create', 'edit', 'error', 'focus'):
            with self.subTest(state=state):
                url = reverse('category_edit', args=[self.child.pk]) if state == 'edit' else reverse('category_create')
                if state == 'error':
                    response = self.client.post(url, {'parent_name': 'サーバー', 'middle_name': '', 'child_name': '保持'})
                    self.assertIn('middle_name', response.context['form'].errors)
                else:
                    response = self.client.get(url, {'focus_category': self.child.pk} if state == 'focus' else {})
                value = response.context['form']['parent_name'].value() or ''
                checks = '''
const selector = document.getElementById('managementCategoryGenre');
const pane = document.querySelector('.category-browser-pane .list-group');
const parent = document.getElementById('id_parent_name');
assert(parent.value === VALUE, 'CRUD initial value');
assert([...parent.querySelectorAll('optgroup')].some(group => group.label === 'システム基盤'), 'real optgroups');
assert([...parent.options].filter(option => option.value === 'サーバー').length === 1, 'no duplicate parents');
if (STATE === 'edit' || STATE === 'focus') {
 assert(selector.value === 'infrastructure', 'editing/focus genre');
 assert(document.querySelector('[data-category-id="CHILD_ID"]').classList.contains('category-editing-target'), 'focus highlight');
 const link = document.querySelector('[data-category-id="CHILD_ID"] a');
 assert(link.getAttribute('href') === EDIT_URL, 'original PK edit URL');
}
if (STATE === 'error') assert(selector.value === 'infrastructure', 'bound error genre');
selector.value = 'client'; change(selector);
assert(visible(pane).includes('PC') && !visible(pane).includes('サーバー'), 'management filter');
assert(parent.value === VALUE, 'switch does not reassign CRUD parent');
selector.value = 'other'; change(selector);
assert(visible(pane).includes('未来の親') && !visible(pane).includes('PC'), 'management fallback');
assert(parent.value === VALUE, 'fallback preserves CRUD parent');
'''.replace('VALUE', json.dumps(value)).replace('STATE', json.dumps(state))
                checks = checks.replace('CHILD_ID', str(self.child.pk)).replace('EDIT_URL', json.dumps(reverse('category_edit', args=[self.child.pk])))
                self.browser(response, checks)

    def test_document_create_edit_error_and_legacy_values(self):
        document = ProjectDocument.objects.create(title='既存', category=self.child.full_name,
                                                  created_by=self.user, is_template=True)
        for state in ('create', 'edit', 'error', 'legacy-error'):
            with self.subTest(state=state):
                url = reverse('project_document_edit', args=[document.pk]) if state == 'edit' else reverse('project_document_create')
                if 'error' in state:
                    payload = {'title': '', 'is_template': 'on', 'parent_category': 'サーバー',
                               'middle_category': 'genre-middle', 'child_category': 'genre-child',
                               'category_selection_id': str(self.child.pk)}
                    if state == 'legacy-error':
                        payload.update(parent_category='PC', middle_category='', child_category='genre-direct', category_selection_id='')
                    response = self.client.post(url, payload)
                    self.assertIn('title', response.context['form'].errors)
                else:
                    response = self.client.get(url)
                expected = {name: str(response.context['form'][name].value() or '') for name in (
                    'parent_category', 'middle_category', 'child_category', 'category_selection_id')}
                checks = '''
const selector = document.getElementById('documentCategoryGenre');
const pane = document.getElementById('documentCategoryParentPane');
const values = () => Object.fromEntries(Object.keys(EXPECTED).map(name => [name, document.getElementById('id_' + name).value]));
assert(JSON.stringify(values()) === JSON.stringify(EXPECTED), 'document initial values');
assert(selector.value === GENRE, 'document edit/error genre');
if (GENRE) assert(document.querySelector('[data-category-id="SELECTED_ID"]').checked, 'document restored selection');
selector.value = 'other'; change(selector);
assert(visible(pane).includes('未来の親') && !visible(pane).includes('PC'), 'document fallback');
assert(JSON.stringify(values()) === JSON.stringify(EXPECTED), 'document switch preserves all hidden originals');
selector.value = 'client'; change(selector);
assert(visible(pane).includes('PC') && !visible(pane).includes('サーバー'), 'document client');
assert(JSON.stringify(values()) === JSON.stringify(EXPECTED), 'document navigation does not select');
pane.querySelector('[data-parent-name="PC"]').click();
if (document.querySelector('[data-category-id="PC_ID"]').checked) document.querySelector('[data-category-id="PC_ID"]').click();
document.querySelector('[data-category-id="PC_ID"]').click();
assert(document.getElementById('id_category_selection_id').value === 'PC_ID', 'document explicit selection');
'''.replace('EXPECTED', json.dumps(expected, ensure_ascii=False))
                checks = checks.replace('GENRE', json.dumps('' if state == 'create' else 'client' if state == 'legacy-error' else 'infrastructure'))
                checks = checks.replace('SELECTED_ID', str(self.pc.pk if state == 'legacy-error' else self.child.pk)).replace('PC_ID', str(self.pc.pk))
                self.browser(response, checks)

    def test_genre_controls_do_not_post_or_mutate_db_and_qr_unchanged(self):
        before = list(FAQCategory.objects.values_list('pk', 'parent_name', 'middle_name', 'child_name'))
        for route in ('article_create', 'tip_create', 'category_create', 'project_document_create'):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)
            selector = re.search(r'<select[^>]*data-category-genre-selector[^>]*>', response.content.decode()).group()
            self.assertNotIn('name=', selector)
        self.assertEqual(before, list(FAQCategory.objects.values_list('pk', 'parent_name', 'middle_name', 'child_name')))
        response = self.client.get(reverse('category_create'), {'tab': 'qr'})
        self.assertNotContains(response, 'data-category-genre-selector')