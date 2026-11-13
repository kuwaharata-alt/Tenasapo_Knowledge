"""Exercise the calendar's actual scroll script without external requests."""

from pathlib import Path
import shutil
import subprocess
import tempfile

from django.test import SimpleTestCase


class ChatScheduleScrollBrowserTests(SimpleTestCase):
    def test_tabs_save_and_restore_main_scroll_without_hijacking_modified_clicks(self):
        candidates = [shutil.which('google-chrome'), shutil.which('chromium'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        source = (Path(__file__).parent / 'templates/tenasapo_knowledge/chat_schedule_settings.html').read_text(encoding='utf-8')
        script = source.split('<script>', 1)[1].split('</script>', 1)[0]
        page = '''<!doctype html><meta charset="utf-8">
<style>html,body{height:100%;margin:0;overflow:hidden}#app-main{height:100vh;overflow:auto}</style>
<main id="app-main"><div style="height:2400px"><nav id="schedule-history-tabs">
<a href="?month=2026-11">active</a><a href="?month=2026-11&history=archive">archive</a>
</nav></div></main><pre id="result">PENDING</pre>
<script>sessionStorage.setItem('chat-schedule-history-scroll', JSON.stringify({
url: location.href, x: 0, y: 430, savedAt: Date.now()}));</script>
<script>ACTUAL_SCRIPT</script>
<script>window.addEventListener('load', () => {
const assert = (ok, message) => {if (!ok) throw new Error(message);};
try {
 const main = document.getElementById('app-main');
 assert(main.scrollTop === 430 && window.scrollY === 0, 'restore app-main position');
 assert(!sessionStorage.getItem('chat-schedule-history-scroll'), 'consume restoration');
 document.querySelectorAll('#schedule-history-tabs a').forEach(link => {
  main.scrollTop = 570;
  link.addEventListener('click', event => event.preventDefault());
  link.click();
  const saved = JSON.parse(sessionStorage.getItem('chat-schedule-history-scroll'));
  assert(saved.url === link.href && saved.y === 570, 'save position for each tab destination');
  sessionStorage.removeItem('chat-schedule-history-scroll');
  link.dispatchEvent(new MouseEvent('click', {ctrlKey: true, bubbles: true, cancelable: true}));
  assert(!sessionStorage.getItem('chat-schedule-history-scroll'), 'leave new-tab clicks alone');
 });
 document.getElementById('result').textContent = 'PASS';
} catch (error) {document.getElementById('result').textContent = 'FAIL: ' + error.message;}
});</script>'''.replace('ACTUAL_SCRIPT', script)
        with tempfile.TemporaryDirectory(prefix='nexus-schedule-scroll-') as directory:
            path = Path(directory) / 'scroll.html'
            path.write_text(page, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                '--no-first-run', '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--disable-component-update', '--disable-sync', '--no-proxy-server',
                '--host-resolver-rules=MAP * ~NOTFOUND',
                '--virtual-time-budget=2000', '--dump-dom', path.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('<pre id="result">PASS</pre>', result.stdout)