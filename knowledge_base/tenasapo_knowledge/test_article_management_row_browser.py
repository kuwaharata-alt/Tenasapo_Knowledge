"""Run the production management row handler headlessly, without external assets."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ArticleManagementRowBrowserTests(unittest.TestCase):
    def test_row_click_preserves_edit_demo_selection_preview_and_single_toggle(self):
        candidates = [
            shutil.which('chromium'), shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if browser is None:
            self.skipTest('Chrome/Edge/Chromium is required')
        template = (Path(__file__).parent / 'templates/tenasapo_knowledge/article_management.html').read_text(encoding='utf-8')
        start = template.index("    document.querySelectorAll('.mgmt-expandable-row')")
        end = template.index("    document.querySelectorAll('.mgmt-title-btn')", start)
        handler = template[start:end]
        fixture = '''<!doctype html><meta charset="utf-8">
<table><tbody><tr class="mgmt-expandable-row">
<td id="title"><span class="mgmt-title-text">Article title</span>
<div class="mgmt-item-meta" id="meta"><span>Contributor</span></div></td>
<td id="space"> </td><td>
<a href="#edit" id="edit"><i>Edit</i></a>
<form class="demo-visibility-form" id="demo-form">
<div id="demo-space">Demo area</div>
<input type="checkbox" id="demo" class="demo-visibility-checkbox">
<label for="demo" id="demo-label"><span>Demo visibility</span></label>
<button type="button" id="demo-button"><i>Save</i></button>
</form><button type="button" id="action"><i>Action</i></button>
<button type="button" class="mgmt-title-btn"><i class="mgmt-toggle-icon">Expand</i></button>
</td></tr><tr><td colspan="6"><div id="preview"><span>Preview body</span></div></td></tr>
</tbody></table><pre id="result">PENDING</pre><script>
'''
        checks = '''
let toggles = 0;
const toggle = document.querySelector('.mgmt-title-btn');
toggle.addEventListener('click', () => toggles++);
const click = selector => document.querySelector(selector).click();
const assertCount = (expected, message) => {
    if (toggles !== expected) throw new Error(message + ': ' + toggles);
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
try {
    click('#title .mgmt-title-text'); assertCount(1, 'title expands once');
    click('#meta span'); assertCount(2, 'metadata collapses once');
    click('#space'); assertCount(3, 'blank space expands once');
    click('#edit i'); assertCount(3, 'edit link must not expand');
    const checkbox = document.getElementById('demo');
    assert(!checkbox.checked, 'demo starts unchecked');
    click('#demo'); assertCount(3, 'demo checkbox must not expand');
    assert(checkbox.checked, 'demo checkbox still changes');
    click('#demo-label span'); assertCount(3, 'demo label and forwarded click must not expand');
    assert(!checkbox.checked, 'demo label still changes checkbox');
    click('#demo-button i'); click('#demo-space'); click('#action i');
    assertCount(3, 'form, demo button and other buttons must not expand');
    click('.mgmt-title-btn'); assertCount(4, 'direct toggle only once');
    click('.mgmt-toggle-icon'); assertCount(5, 'nested toggle icon only once');
    click('#preview span'); assertCount(5, 'preview body must not collapse');
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.querySelector('.mgmt-title-text'));
    selection.removeAllRanges(); selection.addRange(range);
    assert(selection.toString() === 'Article title', 'text selection fixture is active');
    click('#title'); assertCount(5, 'selected text must not expand');
    selection.removeAllRanges();
    document.getElementById('title').addEventListener('click', event => event.preventDefault(), {once: true});
    document.getElementById('title').dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    assertCount(5, 'cancelled action must not expand');
    click('#title'); assertCount(6, 'normal click still works after selection and cancellation');
    document.getElementById('result').textContent = 'PASS: title, metadata, space, edit, demo, label, buttons, preview, selection, single toggle';
} catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + error.message;
}
</script>'''
        with tempfile.TemporaryDirectory(prefix='nexus-management-row-') as directory:
            page = Path(directory) / 'management.html'
            page.write_text(fixture + handler + checks, encoding='utf-8')
            completed = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=1000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            start = completed.stdout.find('<pre id="result">')
            result = completed.stdout[start:completed.stdout.find('</pre>', start)]
            self.assertIn('<pre id="result">PASS:', result, result or completed.stderr[-2000:])