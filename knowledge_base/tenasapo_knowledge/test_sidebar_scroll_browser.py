"""Run actual sidebar scroll persistence JavaScript in a headless browser."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SidebarScrollBrowserTests(unittest.TestCase):
    def test_sidebar_position_survives_navigation_collapse_and_mobile_open(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        assets = (Path(__file__).resolve().parent.parent
                  / 'templates/includes/sidebar_scroll_assets.html').read_text(encoding='utf-8')
        fixture = '''<!doctype html><meta charset="utf-8">
<style>#app-sidebar {height:200px;overflow:auto;width:240px} .content {height:1500px}
.hidden {display:none}</style>
<button id="sidebar-toggle">Toggle</button>
<aside id="app-sidebar" data-scroll-owner="123"><div class="offcanvas-body">
<nav class="sidebar-menu"><a href="#next">Next page</a></nav><div class="content"></div>
</div></aside><pre id="result">PENDING</pre>
<script>
window.desktop = true;
window.matchMedia = () => ({get matches() {return window.desktop;}});
window.saved = new Map([['nexus-sidebar-scroll:123', '420']]);
Object.defineProperty(window, 'sessionStorage', {value: {
    getItem: key => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value)
}});
</script>'''
        checks = '''<script>
(async () => {
 const assert = (ok, text) => {if (!ok) throw new Error(text);};
 const tick = () => new Promise(resolve => setTimeout(resolve, 50));
 const sidebar = document.getElementById('app-sidebar');
 const body = sidebar.querySelector('.offcanvas-body');
 try {
  await tick();
  assert(sidebar.scrollTop === 420, 'restores previous page position');
  sidebar.scrollTop = 610;
  sidebar.dispatchEvent(new Event('scroll'));
  sidebar.querySelector('a').click();
  assert(saved.get('nexus-sidebar-scroll:123') === '610', 'link preserves current position');
  sidebar.scrollTop = 0;
  window.dispatchEvent(new Event('pageshow'));
  await tick();
  assert(sidebar.scrollTop === 610, 'new page restores position');
  sidebar.classList.add('hidden');
  sidebar.scrollTop = 0;
  sidebar.dispatchEvent(new Event('scroll'));
  assert(saved.get('nexus-sidebar-scroll:123') === '610', 'hidden menu cannot overwrite position');
  sidebar.classList.remove('hidden');
  document.getElementById('sidebar-toggle').click();
  await tick();
  assert(sidebar.scrollTop === 610, 'reopening restores position');
  window.desktop = false;
  sidebar.style.overflow = 'hidden';
  body.style.height = '180px'; body.style.overflow = 'auto';
  sidebar.classList.add('show');
  sidebar.dispatchEvent(new Event('shown.bs.offcanvas'));
  await tick();
  assert(body.scrollTop === 610, 'mobile uses offcanvas body');
  body.scrollTop = 720; body.dispatchEvent(new Event('scroll'));
  window.dispatchEvent(new Event('pagehide'));
  assert(saved.get('nexus-sidebar-scroll:123') === '720', 'mobile saves position');
  assert(!saved.has('nexus-sidebar-scroll:other'), 'per-user storage isolated');
  document.getElementById('result').textContent = 'PASS';
 } catch(error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
})();
</script>'''
        with tempfile.TemporaryDirectory(prefix='nexus-sidebar-scroll-') as directory:
            page = Path(directory) / 'sidebar.html'
            page.write_text(fixture + assets + checks, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--virtual-time-budget=3000', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            output = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertEqual(output, '<pre id="result">PASS', output)