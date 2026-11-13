"""Run the actual shared navigation JavaScript in a headless browser."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FormNavigationBrowserTests(unittest.TestCase):
    def test_visible_sections_with_hidden_trailing_fields(self):
        candidates = [
            shutil.which("chromium"),
            shutil.which("google-chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if browser is None:
            self.skipTest("Chrome/Edge/Chromium is required for JavaScript regression tests")
        assets = (
            Path(__file__).resolve().parent.parent
            / "templates/includes/knowledge_form_navigation_assets.html"
        ).read_text(encoding="utf-8")
        fixture = """<!doctype html><meta charset="utf-8">
<style>
html { font-size: 16px; }
body { margin: 0; }
#app-main { position: absolute; top: 60px; height: 300px; width: 900px; overflow: auto; }
.knowledge-form-layout { display: flex; }
.knowledge-form-nav-column { width: 220px; flex-shrink: 0; }
.fields { width: 600px; }
.card-body > .row > div { margin-top: 0; }
.field { height: 90px; }
.category-fields { height: 420px; }
.os-fields { height: 140px; }
.question-fields { height: 450px; }
[data-form-section] { display: block; height: 32px; }
</style>
<main id="app-main"><div class="knowledge-form-layout">
<aside class="knowledge-form-nav-column"><nav class="knowledge-form-nav">
<div class="knowledge-form-nav-links">
<a href="#form-section-title">Title</a>
<a href="#form-section-category">Category</a>
<a href="#form-section-os">OS</a>
<a href="#form-section-question">Question</a>
</div></nav></aside>
<div class="fields"><form><div class="card-body"><div class="row">
<div class="field"><label id="form-section-title" data-form-section>Title</label><input></div>
<div><div id="form-section-category" data-form-section>Category</div></div>
<div class="category-fields">Category browser</div>
<div hidden><input name="tags"></div>
<div><div id="form-section-os" data-form-section>OS</div></div>
<div class="os-fields">OS inputs</div>
<input type="hidden" name="published"><input type="hidden" name="expires">
<div class="question-fields"><label id="form-section-question" data-form-section>Question</label></div>
<input type="hidden" name="final_hidden">
</div></div></form></div></div></main>
<pre id="result">PENDING</pre>
<script>
// Control scheduling only; layout and production callbacks run in Chrome.
window.navigationFrames = [];
window.requestAnimationFrame = callback => window.navigationFrames.push(callback);
</script>
"""
        checks = """<script>
document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('app-main');
    const result = document.getElementById('result');
    const link = name => document.querySelector(`a[href="#form-section-${name}"]`);
    const assert = (value, message) => { if (!value) throw new Error(message); };
    const scrollToSection = (name, offset = 0) => {
        const section = document.getElementById(`form-section-${name}`);
        root.scrollTop += section.getBoundingClientRect().top - root.getBoundingClientRect().top + offset;
        root.dispatchEvent(new Event('scroll'));
        while (window.navigationFrames.length) window.navigationFrames.shift()(performance.now());
    };
    try {
        assert(link('title').classList.contains('is-current'), 'initial title must highlight');
        assert(link('category').classList.contains('is-current'), 'visible category must highlight despite hidden tags');
        assert(!link('title').classList.contains('is-partially-visible'), 'fully visible title must be strong');
        scrollToSection('category', 180);
        assert(link('category').classList.contains('is-current'), 'category body must highlight with its heading offscreen');
        assert(link('category').classList.contains('is-partially-visible'), 'clipped category must be light');
        scrollToSection('os', -100);
        assert(link('category').classList.contains('is-current'), 'category tail must highlight');
        assert(link('os').classList.contains('is-current'), 'OS must highlight despite hidden date inputs');
        assert(!link('os').classList.contains('is-partially-visible'), 'fully visible OS must be strong');
        assert(!link('title').classList.contains('is-current'), 'offscreen title must not highlight');
        assert(document.querySelectorAll('[aria-current="location"]').length === 1, 'only one accessible current item');
        scrollToSection('question', 80);
        assert(link('question').classList.contains('is-current'), 'last section must highlight despite trailing hidden input');
        assert(link('question').classList.contains('is-partially-visible'), 'oversized last section must be light');
        result.textContent = 'PASS: hidden columns, hidden inputs, multiple highlights, full/partial visibility, last section';
    } catch (error) {
        result.textContent = 'FAIL: ' + error.message;
    }
});
</script>"""
        with tempfile.TemporaryDirectory(prefix="nexus-navigation-") as directory:
            page = Path(directory) / "navigation.html"
            page.write_text(fixture + assets + checks, encoding="utf-8")
            completed = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-first-run",
                    "--disable-background-networking",
                    "--run-all-compositor-stages-before-draw",
                    f"--user-data-dir={directory}/profile",
                    "--virtual-time-budget=3000",
                    "--dump-dom",
                    page.as_uri(),
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            start = completed.stdout.find('<pre id="result">')
            result = completed.stdout[start:completed.stdout.find('</pre>', start)]
            self.assertIn('<pre id="result">PASS:', result, result or completed.stderr[-2000:])