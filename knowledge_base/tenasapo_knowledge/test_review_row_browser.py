"""Exercise the production row-click handler in a real browser without external assets."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ReviewRowBrowserTests(unittest.TestCase):
    def test_row_click_preserves_actions_selection_and_single_toggle(self):
        candidates = [
            shutil.which('chromium'), shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if browser is None:
            self.skipTest('Chrome/Edge/Chromium is required')
        template = (Path(__file__).parent / 'templates/tenasapo_knowledge/review_list.html').read_text(encoding='utf-8')
        start = template.index("    document.querySelectorAll('.review-expandable-row')")
        end = template.index("    document.querySelectorAll('.review-title-toggle')", start)
        handler = template[start:end]
        fixture = '''<!doctype html><meta charset="utf-8">
<table><tbody><tr class="review-expandable-row"><td id="title"><span>Article title</span></td>
<td id="meta">Creator</td><td id="space">
<a href="#edit" id="edit">Edit</a><button id="review"><i>Review</i></button>
<button id="status">Approved</button><button class="review-title-toggle"><i>Expand</i></button>
</td></tr><tr><td id="preview">Preview body</td></tr></tbody></table>
<pre id="result">PENDING</pre><script>
'''
        checks = '''
let toggles = 0;
const toggle = document.querySelector('.review-title-toggle');
toggle.addEventListener('click', () => toggles++);
const click = selector => document.querySelector(selector).click();
const assertCount = (expected, message) => {
    if (toggles !== expected) throw new Error(message + ': ' + toggles);
};
try {
    click('#title span'); assertCount(1, 'title expands');
    click('#meta'); assertCount(2, 'metadata collapses');
    click('#space'); assertCount(3, 'blank space expands');
    click('#edit'); click('#review i'); click('#status');
    assertCount(3, 'interactive actions must not expand');
    click('.review-title-toggle i'); assertCount(4, 'icon must toggle only once');
    click('#preview'); assertCount(4, 'expanded body must not collapse');
    const selection = window.getSelection();
    const range = document.createRange(); range.selectNodeContents(document.getElementById('title'));
    selection.addRange(range);
    click('#title'); assertCount(4, 'text selection must not expand');
    selection.removeAllRanges();
    document.getElementById('title').dispatchEvent(new MouseEvent('click', {bubbles: true, button: 1}));
    assertCount(4, 'non-primary click must not expand');
    document.getElementById('title').addEventListener('click', event => event.preventDefault(), {once: true});
    document.getElementById('title').dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    assertCount(4, 'cancelled action must not expand');
    click('#title'); assertCount(5, 'normal click still works');
    document.getElementById('result').textContent = 'PASS: row, actions, icon, preview, selection, cancelled clicks';
} catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + error.message;
}
</script>'''
        with tempfile.TemporaryDirectory(prefix='nexus-review-row-') as directory:
            page = Path(directory) / 'review.html'
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