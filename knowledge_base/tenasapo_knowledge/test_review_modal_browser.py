"""Run the production DOMContentLoaded script in headless Chrome/Edge/Chromium.

Bootstrap itself is not executed: bootstrap.Modal is a recording stub. These
tests cover DOM event delegation and form values, not Bootstrap modal lifecycle,
focus handling, backdrops, or animations. No Django server or external assets
are required.
"""

import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ReviewModalBrowserTests(unittest.TestCase):
    def test_dynamic_reason_safety_comment_preservation_reset_and_submission(self):
        candidates = [
            shutil.which('chromium'), shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if browser is None:
            self.skipTest('Chrome/Edge/Chromium is required')

        template = (Path(__file__).parent / 'templates/tenasapo_knowledge/review_list.html').read_text(encoding='utf-8')
        listener = template.index("document.addEventListener('DOMContentLoaded', function() {")
        start = template.rfind('<script>', 0, listener) + len('<script>')
        end = template.index('</script>', listener)
        production_script = template[start:end]
        self.assertNotIn('{%', production_script)
        self.assertNotIn('{{', production_script)

        reason = '<img src="invalid:" onerror="window.reasonHtmlExecuted = true"><strong>理由 & 詳細</strong>\n次の行'
        fixture = '''<!doctype html><meta charset="utf-8">
<button type="button" id="article-one" class="review-open-btn"
    data-review-id="source-one" data-title="Article one"
    data-approve-url="/review/one/approve/" data-remand-url="/review/one/remand/"
    data-focus-key="one" data-reviewer-comment="保存済みコメント"
    data-ai-review="AI one" data-standard-only="1" data-remand-reason="以前の理由">Review one</button>
<button type="button" id="article-two" class="review-open-btn"
    data-review-id="source-two" data-title="Article two"
    data-approve-url="/review/two/approve/" data-remand-url="/review/two/remand/"
    data-focus-key="two" data-reviewer-comment=""
    data-ai-review="AI two">Review two</button>
<div hidden id="source-one">
    <p>First article</p>
    <button type="button" class="review-text-popup-btn" data-popup-title="差戻し理由"
        data-popup-content="__REASON__"><span>差戻し理由</span></button>
</div>
<div hidden id="source-two"><p>Second article</p></div>
<div id="reviewModal">
    <h5 id="reviewModalLabel"></h5>
    <div id="reviewPreviewBody"></div>
    <input type="checkbox" id="reviewStandardOnly">
    <input type="checkbox" id="reviewVisibleToCustomer">
    <textarea id="reviewAiReview"></textarea>
    <textarea id="reviewReviewerComment"></textarea>
    <div id="reviewRemandReasonBlock" class="d-none">
        <textarea id="reviewRemandComment"></textarea>
        <button type="button" id="reviewRemandSubmitBtn">Confirm remand</button>
    </div>
    <button type="button" id="reviewApproveBtn">Approve</button>
    <button type="button" id="reviewRemandBtn">Remand</button>
</div>
<div id="reviewTextModal">
    <h5 id="reviewTextModalLabel"></h5><div id="reviewTextModalBody"></div>
</div>
<div id="reviewSummaryModal">
    <div id="reviewSummaryTargetTitle"></div><div id="reviewSummaryRemandText"></div>
    <div id="reviewSummaryAiText"></div><div id="reviewSummaryReviewerComment"></div>
</div>
<form id="reviewApproveForm" method="post">
    <input type="hidden" name="standard_contract_only" id="reviewStandardOnlyValue">
    <input type="hidden" name="visible_to_customer" id="reviewVisibleToCustomerValue">
    <input type="hidden" name="ai_review" id="reviewAiReviewValue">
    <input type="hidden" name="reviewer_comment" id="reviewReviewerCommentValue">
    <input type="hidden" name="next" id="reviewApproveNext" value="/review/">
</form>
<form id="reviewRemandForm" method="post">
    <input type="hidden" name="ai_review" id="reviewAiReviewForRemand">
    <input type="hidden" name="reviewer_comment" id="reviewReviewerCommentForRemand">
    <input type="hidden" name="remand_reason" id="reviewRemandReason">
    <input type="hidden" name="next" id="reviewRemandNext" value="/review/">
</form>
<pre id="result">PENDING</pre>
<script>
window.browserErrors = [];
window.addEventListener('error', event => window.browserErrors.push(event.message));
window.addEventListener('unhandledrejection', event => window.browserErrors.push(String(event.reason)));
window.modalShows = [];
window.bootstrap = {
    Modal: class {
        constructor(element) { this.element = element; }
        show() { window.modalShows.push(this.element.id); }
    }
};
window.confirm = () => true;
window.alert = message => { throw new Error('Unexpected alert: ' + message); };
window.submissions = [];
for (const id of ['reviewApproveForm', 'reviewRemandForm']) {
    const form = document.getElementById(id);
    // Intercept native submit without navigation and snapshot the actual fields.
    form.submit = function() {
        window.submissions.push({
            id: this.id,
            action: this.getAttribute('action'),
            values: Object.fromEntries(new FormData(this))
        });
    };
}
</script>
<script>
'''.replace('__REASON__', html.escape(reason, quote=True))
        checks = r'''
</script>
<script>
document.addEventListener('DOMContentLoaded', function() {
    const assert = (condition, message) => {
        if (!condition) throw new Error(message);
    };
    const byId = id => document.getElementById(id);
    const comment = byId('reviewReviewerComment');
    const preview = byId('reviewPreviewBody');
    const expectedReason = __EXPECTED_REASON__;
    try {
        assert(window.browserErrors.length === 0, window.browserErrors.join('; '));
        assert(!preview.querySelector('.review-text-popup-btn'), 'preview starts empty');
        byId('article-one').click();
        assert(comment.value === '保存済みコメント', 'first article loads its saved comment');
        assert(!byId('reviewStandardOnly').checked, 'restricted article must show demo publishing off');
        const button = preview.querySelector('.review-text-popup-btn');
        assert(button, 'article click dynamically inserts the reason button');
        assert(button !== byId('source-one').querySelector('button'), 'button is a new clone');
        assert(!preview.querySelector('.review-popup-detail'), 'detail is initially absent');

        const draft = '  入力中の承認者コメント\n二行目 <b>そのまま</b>  ';
        comment.value = draft;
        comment.dispatchEvent(new Event('input', {bubbles: true}));
        // Click a descendant to exercise closest() as well as event delegation.
        button.querySelector('span').click();
        const detail = button.nextElementSibling;
        assert(detail && detail.classList.contains('review-popup-detail'), 'inline detail created');
        assert(preview.contains(detail) && !detail.hidden, 'reason is visible inline');
        assert(button.getAttribute('aria-expanded') === 'true', 'expanded state is exposed');
        assert(detail.getAttribute('role') === 'region', 'detail has region semantics');
        assert(detail.getAttribute('aria-label') === '差戻し理由', 'detail is labelled');
        assert(detail.textContent === expectedReason, 'HTML and newlines are literal text');
        assert(detail.childElementCount === 0, 'HTML must not create any elements');
        assert(!window.reasonHtmlExecuted, 'HTML handler must not execute');
        assert(comment.value === draft, 'opening reason preserves the exact draft');
        assert(window.modalShows.length === 0, 'inline reason must not open another modal');

        button.click();
        assert(detail.hidden, 'second click closes the inline reason');
        assert(button.getAttribute('aria-expanded') === 'false', 'collapsed state is exposed');
        assert(comment.value === draft, 'closing reason preserves the exact draft');
        button.click();
        assert(!detail.hidden, 'third click reopens the reason');
        assert(preview.querySelectorAll('.review-popup-detail').length === 1, 'detail is reused');
        assert(detail.textContent === expectedReason, 'reopened reason remains safe text');
        assert(comment.value === draft, 'reopening reason preserves the exact draft');
        assert(!byId('source-one').querySelector('.review-popup-detail'), 'source is unchanged');

        byId('reviewApproveBtn').click();
        assert(window.submissions.length === 1, 'approval submits once');
        const approval = window.submissions[0];
        assert(approval.id === 'reviewApproveForm', 'approval uses its own form');
        assert(approval.action === '/review/one/approve/', 'approval targets the selected article');
        assert(byId('reviewReviewerCommentValue').value === draft.trim(), 'approval hidden comment is populated');
        assert(approval.values.reviewer_comment === draft.trim(), 'approval sends the current trimmed draft');
        assert(approval.values.standard_contract_only === '1', 'publishing off preserves demo restriction');
        byId('reviewStandardOnly').checked = true;
        byId('reviewApproveBtn').click();
        assert(window.submissions.pop().values.standard_contract_only === '0', 'publishing on removes demo restriction');
        assert(comment.value === draft, 'approval does not rewrite the editor');

        byId('article-two').click();
        assert(byId('reviewModalLabel').textContent === 'Article two', 'second article is selected');
        assert(preview.textContent.includes('Second article'), 'preview changes articles');
        assert(!preview.querySelector('.review-popup-detail'), 'old inline reason is removed');
        assert(comment.value === '', 'another article resets the previous draft');
        assert(!byId('reviewStandardOnly').checked, 'demo publishing defaults off even for an unrestricted article');
        assert(byId('reviewAiReview').value === 'AI two', 'second article loads its own AI review');

        const remandDraft = '  別記事の承認者コメント\n確認してください  ';
        comment.value = remandDraft;
        comment.dispatchEvent(new Event('input', {bubbles: true}));
        byId('reviewRemandBtn').click();
        assert(!byId('reviewRemandReasonBlock').classList.contains('d-none'), 'remand opens its input');
        assert(comment.value === remandDraft, 'opening remand preserves the comment');
        byId('reviewRemandComment').value = '  修正が必要です  ';
        byId('reviewRemandSubmitBtn').click();
        assert(window.submissions.length === 2, 'remand submits once');
        const remand = window.submissions[1];
        assert(remand.id === 'reviewRemandForm', 'remand uses its own form');
        assert(remand.action === '/review/two/remand/', 'remand targets the second article');
        assert(byId('reviewReviewerCommentForRemand').value === remandDraft.trim(), 'remand hidden comment is populated');
        assert(remand.values.reviewer_comment === remandDraft.trim(), 'remand sends its current trimmed draft');
        assert(remand.values.remand_reason === '修正が必要です', 'remand reason is sent separately');
        assert(comment.value === remandDraft, 'remand does not rewrite the editor');
        assert(window.modalShows.length === 0, 'reason actions never stack modals');
        assert(window.browserErrors.length === 0, window.browserErrors.join('; '));
        byId('result').textContent = 'PASS: dynamic reason toggle, safe text, preserved draft, article reset, approval and remand';
    } catch (error) {
        byId('result').textContent = 'FAIL: ' + error.message;
    }
});
</script>
'''.replace('__EXPECTED_REASON__', json.dumps(reason, ensure_ascii=False))
        with tempfile.TemporaryDirectory(prefix='nexus-review-modal-') as directory:
            page = Path(directory) / 'review.html'
            page.write_text(fixture + production_script + checks, encoding='utf-8')
            completed = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=1000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            start = completed.stdout.find('<pre id="result">')
            result = completed.stdout[start:completed.stdout.find('</pre>', start)]
            self.assertIn('<pre id="result">PASS:', result, result or completed.stderr[-2000:])