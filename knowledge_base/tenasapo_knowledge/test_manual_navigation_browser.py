"""Run the actual isolated manual navigation asset in headless Chrome/Edge."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ManualNavigationBrowserTests(unittest.TestCase):
    def run_browser(self, checks, *, hash_value='', mobile=False, empty=False, missing_root=False):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if not browser:
            self.skipTest('Chrome/Edge is required')
        assets = (Path(__file__).resolve().parent.parent
                  / 'templates/includes/manual_navigation_assets.html').read_text(encoding='utf-8')
        links = ''.join(f'<li><a id="link-{i}" href="#manual-{i}">Section {i}</a></li>' for i in range(8)) if not empty else ''
        sections = ''.join(f'<section class="manual-section" id="manual-{i}" tabindex="-1"><h2 id="manual-{i}-title" tabindex="-1">Section {i}</h2><p>Plain text</p></section>' for i in range(8)) if not empty else ''
        root_id = 'missing-root' if missing_root else 'app-main'
        fixture = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; --tk-border: #ddd; --tk-surface: white; --tk-text: #333; --tk-muted: #666; }}
#{root_id} {{ position: fixed; top: 80px; left: 20px; width: calc(100% - 40px); height: 360px; overflow: auto; overflow-anchor: none; }}
#result {{ position: fixed; bottom: 0; }}
</style></head><body>
<main id="{root_id}"><div class="manual-layout">
<aside class="manual-toc"><nav><h2>Contents</h2><ul class="manual-toc-links">{links}</ul></nav></aside>
<div class="manual-content">{sections}</div></div></main>
<pre id="result">PENDING</pre>
<script>
// Match the existing form-navigation harness: control frame scheduling only.
window.navigationFrames = [];
window.requestAnimationFrame = callback => window.navigationFrames.push(callback);
</script>{assets}
<style>.manual-section {{ height: 300px; margin: 0 0 20px; padding: 12px; border: 1px solid #ddd; }}
@media (max-width: 991.98px) {{ .manual-toc {{ max-height: 100px; }} }}</style>
<script>
window.addEventListener('error', event => {{ document.getElementById('result').textContent = 'FAIL: ' + event.message; }});
document.addEventListener('DOMContentLoaded', async () => {{
 const root = document.getElementById('{root_id}');
 const toc = document.querySelector('.manual-toc');
 const link = i => document.getElementById('link-' + i);
 const section = i => document.getElementById('manual-' + i);
 const assert = (ok, message) => {{ if (!ok) throw new Error(message); }};
 const flushFrames = () => {{
  while (window.navigationFrames.length) window.navigationFrames.shift()(performance.now());
 }};
 const wait = () => new Promise(resolve => setTimeout(() => {{
  root.dispatchEvent(new Event('scroll')); flushFrames(); resolve();
 }}, 120));
 const sectionTop = i => root.scrollTop + section(i).getBoundingClientRect().top - root.getBoundingClientRect().top;
 try {{
  await wait(); await wait();
  {checks}
  document.getElementById('result').textContent = 'PASS';
 }} catch (error) {{ document.getElementById('result').textContent = 'FAIL: ' + error.message; }}
}});
</script></body></html>'''
        with tempfile.TemporaryDirectory(prefix='nexus-manual-nav-') as directory:
            page = Path(directory) / 'manual.html'
            page.write_text(fixture, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', f'--user-data-dir={directory}/profile',
                '--window-size=' + ('390,700' if mobile else '1280,800'),
                '--virtual-time-budget=5000', '--dump-dom', page.as_uri() + hash_value,
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            start = result.stdout.find('<pre id="result">')
            outcome = result.stdout[start:result.stdout.find('</pre>', start)]
            self.assertEqual(outcome, '<pre id="result">PASS', outcome or result.stderr[-2000:])

    def test_desktop_toc_top_matches_content_at_initial_position(self):
        self.run_browser('''
assert(root.scrollTop === 0, 'initial app pane not scrolled');
assert(getComputedStyle(toc).position === 'sticky', 'desktop toc is sticky');
assert(Math.abs(toc.getBoundingClientRect().top - section(0).getBoundingClientRect().top) < 1,
    'toc top aligns with first content card, without sticky top gap');
''')

    def test_scroll_highlights_all_visible_partial_sections_and_single_aria(self):
        self.run_browser('''
root.scrollTo({top: sectionTop(0) + 200, behavior: 'instant'}); await wait();
assert(link(0).classList.contains('is-current'), 'first overlapping section active');
assert(link(1).classList.contains('is-current'), 'second overlapping section active');
assert(link(0).classList.contains('is-partially-visible'), 'first partial');
assert(link(1).classList.contains('is-partially-visible'), 'second partial');
assert(toc.querySelectorAll('[aria-current="location"]').length === 1, 'single aria-current');
assert(link(0).getAttribute('aria-current') === 'location', 'first visible owns aria');
root.scrollTo({top: sectionTop(6) - 16, behavior: 'instant'}); await wait();
const saved = root.scrollTop;
assert(!link(0).classList.contains('is-current'), 'old highlight cleared');
assert(link(6).classList.contains('is-current'), 'scrolled section active');
assert(!link(6).classList.contains('is-partially-visible'), 'fully visible not partial');
assert(toc.scrollTop > 0, 'active item brought into bounded toc');
assert(root.scrollTop === saved && window.scrollY === 0, 'toc reveal does not move page');
const navBox = toc.getBoundingClientRect(), mainBox = root.getBoundingClientRect();
assert(navBox.top >= mainBox.top && navBox.bottom <= mainBox.bottom + 1, 'sticky toc bounded by app-main');
''')

    def test_click_uses_root_smooth_scroll_focus_and_updates_hash(self):
        self.run_browser('''
let requested = null;
const original = root.scrollTo.bind(root);
const expected = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
// Settle smooth animation deterministically, retaining the actual root geometry.
root.scrollTo = options => { requested = options; original({...options, behavior: 'instant'}); };
link(5).click(); await wait();
assert(requested && requested.behavior === expected, 'root scroll respects motion preference');
assert(Math.abs(section(5).getBoundingClientRect().top - root.getBoundingClientRect().top - 16) < 2, 'root target offset');
assert(document.activeElement.id === 'manual-5-title', 'heading focus');
assert(location.hash === '#manual-5', 'click records hash');
assert(link(5).getAttribute('aria-current') === 'location', 'clicked section active');
assert(window.scrollY === 0, 'window not scrolled');
const media = window.matchMedia;
window.matchMedia = query => query === '(prefers-reduced-motion: reduce)' ? {matches: false} : media(query);
link(2).click(); await wait();
assert(requested.behavior === 'smooth', 'smooth root scroll requested without reduced motion');
window.matchMedia = query => query === '(prefers-reduced-motion: reduce)' ? {matches: true} : media(query);
link(3).click(); await wait();
assert(requested.behavior === 'instant', 'reduced motion is respected');
''')

    def test_initial_hash_hashchange_back_forward_and_invalid_hash(self):
        self.run_browser('''
assert(document.activeElement.id === 'manual-3-title', 'initial hash focused after layout');
assert(Math.abs(root.scrollTop - (sectionTop(3) - 16)) < 2, 'initial root position');
location.hash = '#manual-6'; await wait(); await wait();
assert(document.activeElement.id === 'manual-6-title', 'hashchange focuses target');
assert(link(6).getAttribute('aria-current') === 'location', 'hashchange highlights target');
history.back(); await wait(); await wait();
assert(location.hash === '#manual-3', 'back restores hash');
assert(document.activeElement.id === 'manual-3-title', 'back focuses original target');
history.forward(); await wait(); await wait();
assert(document.activeElement.id === 'manual-6-title', 'forward follows target');
const before = root.scrollTop;
location.hash = '#missing'; await wait();
assert(root.scrollTop === before, 'unknown hash safe');
location.hash = '#%E0%A4%A'; await wait();
assert(root.scrollTop === before, 'malformed hash safe');
''', hash_value='#manual-3')

    def test_modified_and_non_primary_clicks_are_not_intercepted(self):
        self.run_browser('''
let calls = 0;
root.scrollTo = () => { calls++; };
for (const options of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}]) {
 let intercepted = null;
 link(4).addEventListener('click', event => {
  intercepted = event.defaultPrevented; event.preventDefault();
 }, {once: true});
 link(4).dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, ...options}));
 assert(intercepted === false, 'modified link retains browser behavior');
}
assert(calls === 0 && location.hash === '', 'no modified navigation');
''')

    def test_empty_sections_and_missing_root_are_safe(self):
        for missing in (False, True):
            with self.subTest(missing_root=missing):
                self.run_browser('''
window.dispatchEvent(new Event('resize')); window.dispatchEvent(new HashChangeEvent('hashchange'));
await wait(); assert(!toc.querySelector('.is-current'), 'empty navigation safe');
''', empty=True, missing_root=missing)

    def test_small_viewport_resize_and_content_observation_do_not_navigate(self):
        self.run_browser('''
assert(getComputedStyle(toc).position === 'static', 'mobile toc not sticky');
assert(toc.clientHeight <= 100, 'mobile toc bounded');
assert(document.documentElement.scrollWidth <= window.innerWidth, 'no horizontal overflow');
const original = root.scrollTo.bind(root);
root.scrollTo = options => original({...options, behavior: 'instant'});
link(4).click(); await wait();
assert(document.activeElement.id === 'manual-4-title', 'mobile click focus');
const before = root.scrollTop;
root.style.height = '310px'; window.dispatchEvent(new Event('resize')); await wait();
assert(root.scrollTop === before, 'ordinary resize does not follow hash');
section(7).style.height = '500px'; await wait();
assert(root.scrollTop === before, 'content resize does not navigate');
section(4).style.height = '40px'; await wait();
assert(link(5).classList.contains('is-current'), 'ResizeObserver refreshes visible sections');
assert(toc.querySelectorAll('[aria-current="location"]').length === 1, 'mobile single aria');
''', mobile=True)