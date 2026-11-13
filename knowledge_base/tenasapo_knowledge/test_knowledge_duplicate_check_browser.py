"""Exercise the real duplicate-check includes with only fetch/Bootstrap mocked."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


class KnowledgeDuplicateCheckBrowserTests(unittest.TestCase):
    def run_browser_checks(self, checks, registration_assets='', form_id='registrationForm'):
        candidates = [
            shutil.which('chromium'), shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if browser is None:
            self.skipTest('Chrome/Edge/Chromium is required for JavaScript regression tests')
        includes = Path(__file__).resolve().parent.parent / 'templates' / 'includes'
        modal = (includes / 'knowledge_duplicate_check.html').read_text(encoding='utf-8')
        # Keep the actual markup; only resolve the single Django URL tag for this file fixture.
        modal, count = re.subn(
            r"\{%\s*url\s+['\"]knowledge_duplicate_candidates['\"]\s*%\}",
            '/knowledge/duplicate-candidates/', modal,
        )
        self.assertEqual(count, 1)
        assets = (includes / 'knowledge_duplicate_check_assets.html').read_text(encoding='utf-8')
        button = (includes / 'knowledge_duplicate_check_button.html').read_text(encoding='utf-8')
        fixture = r'''<!doctype html><meta charset="utf-8">
<form id="registrationForm">
  <input type="hidden" name="registered_category" value="11">
  <div id="selectedOtherBranch"><input type="hidden" name="registered_category" value="22"></div>
  <input type="hidden" name="registered_category" value="11">
  <input type="checkbox" name="registered_category" value="999" checked>
  <input type="text" name="registered_category" value="888">
  <textarea name="body">unsaved body</textarea><input type="file" name="attachment">
</form>
<pre id="result">PENDING</pre>
<script>
window.requests = [];
window.responsePlans = [];
window.modalEvents = [];
window.modalIsHidden = true;
window.modalIsHiding = false;
// Fetch is the only network boundary; deferred plans intentionally ignore abort to test stale guards.
window.fetch = (url, options) => {
    const request = {url: new URL(url, location.href), options};
    requests.push(request);
    const plan = responsePlans.shift();
    if (!plan) return Promise.reject(new Error('Unexpected request: ' + url));
    return plan(request);
};
const instances = new WeakMap();
window.bootstrap = {Modal: {getOrCreateInstance(element) {
    if (!instances.has(element)) {
        const emit = name => {
            modalEvents.push(name);
            element.dispatchEvent(new Event(name));
        };
        instances.set(element, {
            show() {
                window.modalIsHidden = false;
                element.classList.add('show');
                element.removeAttribute('aria-hidden');
                emit('show.bs.modal');
                setTimeout(() => emit('shown.bs.modal'), 5);
            },
            hide() {
                if (window.modalIsHiding || window.modalIsHidden) return;
                window.modalIsHiding = true;
                emit('hide.bs.modal');
                setTimeout(() => {
                    element.classList.remove('show');
                    element.setAttribute('aria-hidden', 'true');
                    window.modalIsHidden = true;
                    window.modalIsHiding = false;
                    emit('hidden.bs.modal');
                }, 10);
            }
        });
    }
    return instances.get(element);
}}};
document.addEventListener('click', event => {
    if (event.target.closest('[data-bs-dismiss="modal"]')) {
        bootstrap.Modal.getOrCreateInstance(document.getElementById('knowledgeDuplicateModal')).hide();
    }
});
</script>'''
        fixture = fixture.replace('</form>', button + '</form>')
        fixture = fixture.replace('id="registrationForm"', f'id="{form_id}"')
        harness = r'''<script>
document.addEventListener('DOMContentLoaded', async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
    const waitFor = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt++) {
            if (predicate()) return;
            await sleep(5);
        }
        throw new Error('Timed out: ' + message);
    };
    const form = document.getElementById('registrationForm');
    const modal = document.getElementById('knowledgeDuplicateModal');
    const byId = suffix => document.getElementById('knowledgeDuplicate' + suffix);
    const proceed = byId('Continue');
    const retry = byId('Retry');
    const rows = () => byId('Results').children;
    const plan = data => responsePlans.push(() => Promise.resolve({
        ok: true, json: () => Promise.resolve(data)
    }));
    const candidate = (kind, code, title = kind + ' title') => ({
        kind, management_code: code, title, categories: ['PC/設定'],
        url: '/detail/' + code + '/', updated_at: '2026-11-11T00:00:00Z'
    });
    const envelope = (results = [], page = 1, pages = 1, total = results.length) => ({
        categories: ['PC/設定', '別カテゴリ/操作'], results, total, page, pages
    });
    const loaded = () => waitFor(() => !modal.hasAttribute('aria-busy'), 'load completion');
    const close = async () => {
        modal.querySelector('[data-bs-dismiss="modal"]').click();
        await waitFor(() => modalIsHidden, 'modal close');
    };
    const open = async callback => {
        const shownBefore = modalEvents.filter(name => name === 'shown.bs.modal').length;
        window.confirmKnowledgeDuplicates(form, callback);
        await waitFor(() => modalEvents.filter(name => name === 'shown.bs.modal').length > shownBefore,
                      'shown event');
    };
    const setIds = ids => {
        form.querySelectorAll('input[type="hidden"][name="registered_category"]').forEach(input => input.remove());
        ids.forEach(id => {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = 'registered_category'; input.value = id;
            form.appendChild(input);
        });
    };
    try {
        await waitFor(() => typeof window.confirmKnowledgeDuplicates === 'function', 'production initializer');
        CHECKS_PLACEHOLDER
        assert(responsePlans.length === 0, 'all expected requests consumed');
        document.getElementById('result').textContent = 'PASS: duplicate checks';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + error.message;
    }
});
</script>'''.replace('CHECKS_PLACEHOLDER', checks).replace(
            "getElementById('registrationForm')", f"getElementById('{form_id}')",
        )
        with tempfile.TemporaryDirectory(prefix='nexus-duplicate-check-') as directory:
            page = Path(directory) / 'duplicate.html'
            page.write_text(fixture + modal + assets + registration_assets + harness, encoding='utf-8')
            completed = subprocess.run(
                [
                    browser, '--headless=new', '--disable-gpu', '--no-sandbox',
                    '--no-first-run', '--disable-background-networking',
                    '--run-all-compositor-stages-before-draw',
                    f'--user-data-dir={directory}/profile', '--virtual-time-budget=6000',
                    '--dump-dom', page.as_uri(),
                ],
                capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            start = completed.stdout.find('<pre id="result">')
            result = completed.stdout[start:completed.stdout.find('</pre>', start)]
            self.assertIn('<pre id="result">PASS:', result, result or completed.stderr[-2000:])

    def test_category_button_checks_without_validation_or_preview_and_requires_category(self):
        self.run_browser_checks(r'''
        const button = form.querySelector('[data-knowledge-duplicate-check]');
        const feedback = form.querySelector('[data-knowledge-duplicate-feedback]');
        let submissions = 0;
        form.addEventListener('submit', event => { event.preventDefault(); submissions++; });
        form.elements.body.required = true;
        form.elements.body.value = '';
        setIds([]);
        button.click();
        assert(!feedback.hidden, 'missing category is explained inline');
        assert(requests.length === 0 && modalIsHidden, 'missing category sends no request');
        setIds(['11', '22']);
        plan(envelope([candidate('FAQ', 'FQ00001')]));
        button.click();
        await waitFor(() => !proceed.disabled, 'manual check loaded');
        assert(feedback.hidden, 'category selection clears feedback');
        assert(proceed.hidden, 'manual check cannot open preview');
        assert(rows().length === 1, 'manual check displays candidates with incomplete form');
        assert(requests[0].url.searchParams.getAll('registered_category').sort().join(',') === '11,22',
               'manual check uses all selected categories');
        assert(submissions === 0, 'manual check never submits');
        await close();
        plan(envelope());
        button.click();
        await waitFor(() => !proceed.disabled, 'manual zero results');
        assert(byId('Status').textContent.includes('入力を続けられます'), 'manual empty message');
        await close();
        let previews = 0;
        plan(envelope());
        await open(() => previews++);
        await loaded();
        assert(!proceed.hidden, 'registration gate restores preview action');
        proceed.click();
        await waitFor(() => previews === 1, 'normal registration still proceeds');
        assert(submissions === 0, 'manual actions preserve form');
        ''')

    def test_candidates_all_hidden_selections_safe_text_and_continue_after_hidden(self):
        self.run_browser_checks(r'''
        let calls = 0;
        const callback = () => {
            assert(modalIsHidden && !modalIsHiding, 'preview opens only after modal is fully hidden');
            assert(modalEvents.at(-1) === 'hidden.bs.modal', 'hidden event precedes preview');
            calls++;
        };
        const unsafe = '<img id="duplicate-xss" src=x onerror="window.xssRan=true">';
        const data = envelope([
            candidate('FAQ', 'FQ00001', unsafe), candidate('Tips', 'TP00001', 'Tips candidate')
        ]);
        data.categories = [unsafe, '別カテゴリ/操作'];
        data.results[0].categories = [unsafe];
        plan(data);
        await open(callback);
        await loaded();
        assert(requests.length === 1, 'one initial request');
        const selected = requests[0].url.searchParams.getAll('registered_category').sort();
        assert(JSON.stringify(selected) === JSON.stringify(['11', '22']),
               'all selected hidden inputs, across branches, not checked pane checkbox');
        assert(requests[0].url.searchParams.get('page') === '1', 'initial page');
        assert(requests[0].options.cache === 'no-store', 'no browser response caching');
        assert(requests[0].options.credentials === 'same-origin', 'authenticated request');
        assert(rows().length === 2, 'both FAQ and Tips render');
        const links = Array.from(byId('Results').querySelectorAll('a'));
        assert(links.length === 2, 'two detail links');
        for (const [index, link] of links.entries()) {
            assert(link.target === '_blank', 'detail opens in a new tab');
            assert(link.relList.contains('noopener') && link.relList.contains('noreferrer'), 'safe tab link');
            assert(link.getAttribute('href').endsWith(data.results[index].url), 'correct detail URL');
        }
        assert(rows()[0].querySelector('.badge').textContent === 'Q&A' && rows()[1].querySelector('.badge').textContent === 'Tips',
               'kind labels render');
        assert(links[0].textContent.includes(unsafe), 'title is literal text');
        assert(byId('Categories').textContent.includes(unsafe), 'category is literal text');
        assert(byId('Results').textContent.includes(unsafe), 'matched category is literal text');
        assert(!document.getElementById('duplicate-xss') && !window.xssRan, 'no injected element or script');
        assert(!proceed.disabled, 'successful candidates enable preview');
        assert(calls === 0, 'loading alone must not open preview');
        proceed.click();
        assert(calls === 0 && modalIsHiding, 'preview must wait for asynchronous hide');
        proceed.click();
        await waitFor(() => calls === 1, 'one callback');
        modal.dispatchEvent(new Event('hidden.bs.modal'));
        assert(calls === 1, 'double clicks and repeated hidden events cannot duplicate callback');
        assert(form.elements.body.value === 'unsaved body', 'draft form is preserved');
        assert(form.querySelector('input[type="file"]'), 'attachment input is preserved');

        plan(envelope());
        await open(callback);
        await loaded();
        assert(rows().length === 0 && !proceed.disabled, 'zero results still enable preview');
        assert(byId('Status').textContent.includes('見つかりません'), 'empty success explained');
        proceed.click();
        assert(calls === 1, 'second preview also waits for hidden');
        await waitFor(() => calls === 2, 'empty result callback');
        ''')

    def test_registration_gate_preview_and_final_submit_for_both_forms(self):
        templates = Path(__file__).resolve().parent / 'templates' / 'tenasapo_knowledge'
        for kind, filename, form_id, variable, sync in (
            ('Faq', 'article_form.html', 'articleMainForm', 'articleMainForm', 'syncArticleFormBeforeSubmit'),
            ('Tips', 'tips_form.html', 'tipsMainForm', 'tipsMainForm', 'syncTipsFormBeforeSubmit'),
        ):
            with self.subTest(kind=kind):
                source = (templates / filename).read_text(encoding='utf-8')
                start = source.index(f'    if ({variable}) {{\n        {variable}.addEventListener(\'submit\'')
                end = source.index('    // ===== ', start)
                submit_and_preview = source[start:end]
                confirm_start = source.index("        confirmBtn.addEventListener('click', function() {")
                confirm_end = source.index(f'        window.render{kind}Preview = ', confirm_start)
                final_submit = source[confirm_start:confirm_end]
                prefix = 'faq' if kind == 'Faq' else 'tips'
                registration_assets = f'''
<div id="{prefix}PreviewModal"><button type="button" id="{prefix}PreviewConfirmBtn">Confirm</button></div>
<script>
window.previewCalls = 0;
window.saved = 0;
document.addEventListener('DOMContentLoaded', function() {{
    const {variable} = document.getElementById('{form_id}');
    let is{kind}PreviewConfirmed = false;
    let render{kind}PreviewRequest = null;
    const {sync} = () => {{}};
    // This isolated gate fixture has no block controls; rendered-form tests
    // exercise validation together with the real block editor separately.
    window.KnowledgeBlockEditors = {{validate: () => true}};
    {submit_and_preview}
    const confirmBtn = document.getElementById('{prefix}PreviewConfirmBtn');
    const previewModalEl = document.getElementById('{prefix}PreviewModal');
    {final_submit}
    window.render{kind}Preview = function() {{
        window.previewCalls++;
        render{kind}PreviewRequest = Promise.resolve();
        return render{kind}PreviewRequest;
    }};
    {variable}.addEventListener('submit', event => {{
        if (!event.defaultPrevented) {{
            event.preventDefault();
            window.saved++;
        }}
    }});
}});
</script>'''
                checks = f'''
        const preview = document.getElementById('{prefix}PreviewModal');
        plan(envelope([candidate('FAQ', 'FQ00001')]));
        form.requestSubmit();
        await waitFor(() => !proceed.disabled, 'duplicate gate ready');
        assert(window.saved === 0 && window.previewCalls === 0, 'initial submit cannot preview or save');
        assert(modal.classList.contains('show'), 'duplicate gate opens before preview');
        await close();
        assert(window.saved === 0 && window.previewCalls === 0, 'back leaves form unsaved');
        plan(envelope());
        form.requestSubmit();
        await waitFor(() => !proceed.disabled, 'reopened gate ready');
        proceed.click();
        await waitFor(() => preview.classList.contains('show'), 'preview opens after duplicate confirmation');
        assert(!modal.classList.contains('show'), 'duplicate modal is hidden before preview');
        assert(window.previewCalls === 1 && window.saved === 0, 'preview does not save');
        document.getElementById('{prefix}PreviewConfirmBtn').click();
        assert(window.saved === 1, 'final preview confirmation saves exactly once');
        assert(requests.length === 2, 'final save does not reopen duplicate gate');
        assert(form.elements.body.value === 'unsaved body', 'draft fields survive both modals');
                '''
                self.run_browser_checks(checks, registration_assets, form_id)

    def test_empty_malformed_http_and_network_failures_block_until_retry_succeeds(self):
        self.run_browser_checks(r'''
        let calls = 0;
        const failures = [
            () => Promise.reject(new TypeError('network unavailable')),
            () => Promise.resolve({ok: false, status: 403, json: () => Promise.resolve({error: 'denied'})}),
            () => Promise.resolve({ok: true, json: () => Promise.reject(new SyntaxError('empty response'))}),
            () => Promise.resolve({ok: true, json: () => Promise.reject(new SyntaxError('malformed JSON'))}),
            () => Promise.resolve({ok: true, json: () => Promise.resolve({})}),
            () => Promise.resolve({ok: true, json: () => Promise.resolve(null)}),
        ];
        for (const failure of failures) {
            responsePlans.push(failure);
            await open(() => calls++);
            await loaded();
            assert(proceed.disabled, 'error must block preview');
            assert(!retry.hidden, 'error offers retry');
            assert(rows().length === 0, 'error must not leave candidate rows');
            assert(byId('Status').textContent.includes('取得できません'), 'error explained');
            proceed.click();
            assert(calls === 0, 'disabled proceed cannot invoke callback');
            plan(envelope());
            const before = requests.length;
            retry.click();
            assert(proceed.disabled, 'retry blocks proceed while loading');
            await loaded();
            assert(requests.length === before + 1, 'retry sends one new request');
            assert(!proceed.disabled && retry.hidden, 'successful retry clears error and enables preview');
            await close();
            assert(calls === 0, 'dismiss is never confirmation');
        }
        ''')

    def test_pagination_replaces_rows_and_maintains_selected_categories(self):
        self.run_browser_checks(r'''
        plan(envelope(Array.from({length: 20}, (_, i) => candidate('FAQ', 'FQ' + String(i).padStart(5, '0'))),
                      1, 2, 21));
        await open(() => { throw new Error('pagination must not confirm preview'); });
        await loaded();
        assert(rows().length === 20, 'first page has twenty rows');
        assert(byId('Previous').disabled && !byId('Next').disabled, 'first page navigation');
        assert(byId('Page').textContent.includes('1 / 2'), 'first page indicator');
        plan(envelope([candidate('Tips', 'TP99999', 'last page')], 2, 2, 21));
        byId('Next').click();
        assert(proceed.disabled, 'page loading blocks preview');
        await loaded();
        assert(rows().length === 1 && byId('Results').textContent.includes('last page'), 'rows replaced');
        assert(!byId('Results').textContent.includes('FQ00000'), 'no previous page leftovers');
        assert(!byId('Previous').disabled && byId('Next').disabled, 'last page navigation');
        assert(byId('Page').textContent.includes('2 / 2'), 'last page indicator');
        plan(envelope([candidate('FAQ', 'FQ00000')], 1, 2, 21));
        byId('Previous').click();
        await loaded();
        assert(requests.map(request => request.url.searchParams.get('page')).join(',') === '1,2,1',
               'next and previous request correct pages');
        for (const request of requests) {
            assert(request.url.searchParams.getAll('registered_category').sort().join(',') === '11,22',
                   'pagination retains all selected categories');
        }
        await close();
        ''')

    def test_changed_selection_rechecks_and_closed_stale_requests_cannot_overwrite(self):
        self.run_browser_checks(r'''
        let calls = 0;
        plan(envelope([candidate('FAQ', 'FQ00001', 'original selection')]));
        await open(() => calls++);
        await loaded();
        setIds(['33', '44']);
        let resolveChanged;
        responsePlans.push(() => new Promise(resolve => { resolveChanged = resolve; }));
        proceed.click();
        assert(calls === 0 && !modalIsHiding, 'changed selection rechecks rather than confirms');
        assert(proceed.disabled, 'changed selection blocks preview');
        assert(requests.at(-1).url.searchParams.getAll('registered_category').sort().join(',') === '33,44',
               'changed hidden selections requested');
        resolveChanged({ok: true, json: () => Promise.resolve(envelope())});
        await loaded();
        await close();
        assert(calls === 0, 'closing after recheck does not confirm');

        let resolveStale;
        responsePlans.push(() => new Promise(resolve => { resolveStale = resolve; }));
        await open(() => { throw new Error('obsolete callback'); });
        const staleRequest = requests.at(-1);
        assert(proceed.disabled, 'pending request blocks preview');
        await close();
        assert(staleRequest.options.signal.aborted, 'close aborts old request');
        setIds(['55']);
        const fresh = envelope([candidate('Tips', 'TP00055', 'fresh selection')]);
        fresh.categories = ['最新カテゴリ'];
        plan(fresh);
        await open(() => calls++);
        await loaded();
        assert(requests.at(-1).url.searchParams.getAll('registered_category').join(',') === '55',
               'reopen reads updated categories');
        assert(requests.at(-1).url.searchParams.get('page') === '1', 'reopen resets page');
        const stale = envelope([candidate('FAQ', 'FQ00033', 'STALE RESULT')]);
        stale.categories = ['STALE CATEGORY'];
        resolveStale({ok: true, json: () => Promise.resolve(stale)});
        await sleep(25);
        assert(byId('Results').textContent.includes('fresh selection'), 'fresh results preserved');
        assert(!byId('Results').textContent.includes('STALE'), 'old response cannot overwrite rows');
        assert(byId('Categories').textContent.includes('最新カテゴリ'), 'fresh categories preserved');
        assert(!byId('Categories').textContent.includes('STALE'), 'old response cannot overwrite categories');
        assert(!proceed.disabled, 'old response cannot disable fresh success');
        proceed.click();
        assert(calls === 0, 'fresh preview waits for hide');
        await waitFor(() => calls === 1, 'only latest callback');
        ''')