"""Execute row-click behavior with actual shared JavaScript in Chrome/Edge."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class UserListRowBrowserTests(unittest.TestCase):
    def test_row_click_excludes_history_actions_and_text_selection(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not browser:
            self.skipTest('Chrome/Edge is required')
        assets = (Path(__file__).resolve().parent.parent
                  / 'templates/includes/user_list_row_assets.html').read_text(encoding='utf-8')
        fixture = '''<!doctype html><meta charset="utf-8">
<table><tbody id="userListAccordion">
<tr data-user-detail-target="detail"><td><span id="name">User name</span>
<button id="history"><i id="icon">History</i></button><a id="edit" href="#edit">Edit</a>
<form><button id="action" type="button">Action</button></form><time id="time">2026/11/12</time></td></tr>
<tr><td><div id="detail"><span id="inside">Details</span></div></td></tr>
</tbody></table><pre id="result">PENDING</pre>
<script>
window.calls = 0;
window.bootstrap = {Collapse: {getOrCreateInstance: (element, options) => {
 if (element.id !== 'detail' || options.toggle !== false) throw new Error('wrong target');
 return {toggle: () => {window.calls++;}};
}}};
</script>'''
        checks = '''<script>
document.addEventListener('DOMContentLoaded', () => {
 const click = id => document.getElementById(id).click();
 const assert = (ok, message) => {if (!ok) throw new Error(message);};
 try {
  click('name'); click('time'); assert(calls === 2, 'name and timestamp toggle');
  click('icon'); click('edit'); click('action'); click('inside');
  assert(calls === 2, 'actions and detail content do not toggle');
  const range = document.createRange(); range.selectNodeContents(document.getElementById('name'));
  getSelection().addRange(range); click('name'); assert(calls === 2, 'selection does not toggle');
  getSelection().removeAllRanges(); click('name'); assert(calls === 3, 'repeat click toggles');
  delete window.bootstrap; click('name'); assert(calls === 3, 'missing bootstrap safe');
  document.getElementById('result').textContent = 'PASS';
 } catch (error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
});
</script>'''
        with tempfile.TemporaryDirectory(prefix='nexus-user-row-') as directory:
            page = Path(directory) / 'row.html'
            page.write_text(fixture + assets + checks, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=2000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('<pre id="result">PASS</pre>', result.stdout)