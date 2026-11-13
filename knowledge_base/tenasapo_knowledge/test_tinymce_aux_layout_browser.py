"""Offline CSS regression using simulated, not real, TinyMCE auxiliary output.

Chrome supplies layout and hit testing; no Django server, CDN, Bootstrap, or
TinyMCE runtime is loaded. The two empty body-level nodes model the observed
TinyMCE inline styles. Only the first style block from the current base template
is under test; the small fixture stylesheet supplies deterministic dimensions
and the missing Bootstrap/offcanvas scaffolding, never auxiliary positioning.
"""

import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


FIXTURE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style id="production-css">__BASE_CSS__</style>
<style>
/* Fixed row sizes leave two unoccupied cells in the collapsed desktop grid:
   row 2 / column 1 and row 3 / column 1. Empty relative auxiliary nodes stretch
   into those cells; their inline viewport width overflows the zero-width column.
   No fixture rule places, sizes, or disables pointer events on auxiliary nodes. */
* { box-sizing: border-box; }
html { font-size: 16px; }
body > .navbar { height: 64px; display: flex; align-items: center;
    justify-content: space-between; padding: 8px 16px; }
.app-page-header { height: 112px; padding: 16px; }
.app-footer { height: 48px; }
.app-content { padding: 24px 16px; }
label { display: block; margin-bottom: 8px; }
input, select { display: block; width: 240px; height: 36px; margin-bottom: 20px; }
button { min-height: 32px; }
.offcanvas-header { padding: 8px; }
@media (min-width: 992px) {
    [data-bs-toggle="offcanvas"], [data-bs-dismiss="offcanvas"] { display: none; }
}
@media (max-width: 991.98px) {
    #sidebar-toggle { display: none; }
    .offcanvas-lg { position: fixed; top: 0; bottom: 0; left: 0;
        width: var(--bs-offcanvas-width); z-index: 1045; visibility: hidden;
        transform: translateX(-100%); }
    .offcanvas-lg.show { visibility: visible; transform: none; }
}
</style></head><body class="has-sidebar sidebar-collapsed">
<nav class="navbar navbar-expand-lg border-bottom">
  <button id="sidebar-toggle" type="button" aria-controls="app-sidebar"
      aria-expanded="false">Desktop menu</button>
  <button id="mobile-toggle" type="button" data-bs-toggle="offcanvas"
      data-bs-target="#app-sidebar" aria-controls="app-sidebar"
      aria-expanded="false">Mobile menu</button>
  <button id="logout" class="dropdown-item" type="button">Logout</button>
</nav>
<aside class="app-sidebar offcanvas-lg offcanvas-start" id="app-sidebar"
    tabindex="-1" aria-labelledby="sidebar-title" data-scroll-owner="fixture">
  <div class="offcanvas-header"><h2 id="sidebar-title">Menu</h2>
    <button id="sidebar-close" type="button" data-bs-dismiss="offcanvas"
        data-bs-target="#app-sidebar">Close</button></div>
  <div class="offcanvas-body"><nav class="sidebar-menu">
    <a id="sidebar-link" href="#fixture">Fixture item</a>
  </nav></div>
</aside>
<section class="app-page-header container" aria-label="Page title">
  <div class="page-title-panel"><h1 id="page-title" class="page-title-main">Title</h1></div>
</section>
<main class="app-main" id="app-main"><div class="app-content container py-4">
  <label for="title-input">Title input</label>
  <input id="title-input" class="form-control" autocomplete="off">
  <label for="category">Category</label>
  <select id="category" class="form-select"><option>First</option><option>Second</option></select>
</div></main>
<footer class="app-footer">Fixture footer</footer>
<div class="tox tox-silver-sink tox-tinymce-aux" style="position: relative; width: 100vw; z-index: 1300;"></div>
<div class="tox tox-silver-sink tox-tinymce-aux" style="position: relative; width: 100vw; z-index: 1300;"></div>
<script>
const negativeControl = __NEGATIVE__;
const report = {mode: negativeControl ? 'negative' : 'fixed', states: []};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const byId = id => document.getElementById(id);
const rect = element => {
    const r = element.getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height};
};
const near = (a, b) => Math.abs(a - b) < 1;
const aux = [...document.querySelectorAll('body > .tox-tinymce-aux')];
const hits = {};
document.addEventListener('click', event => {
    hits[event.target.id] = (hits[event.target.id] || 0) + 1;
    if (event.target.closest('a')) event.preventDefault();
});
// Local state changes simulate Bootstrap offcanvas and the base template's
// collapsed class contract. This is a CSS test, not a Bootstrap/JS integration test.
byId('sidebar-toggle').addEventListener('click', () => {
    const collapsed = document.body.classList.toggle('sidebar-collapsed');
    byId('sidebar-toggle').setAttribute('aria-expanded', String(!collapsed));
});
byId('mobile-toggle').addEventListener('click', () => {
    byId('app-sidebar').classList.add('show');
    byId('mobile-toggle').setAttribute('aria-expanded', 'true');
});
byId('sidebar-close').addEventListener('click', () => {
    byId('app-sidebar').classList.remove('show');
    byId('mobile-toggle').setAttribute('aria-expanded', 'false');
});
function hitAtCenter(id) {
    const r = byId(id).getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    assert(r.width > 0 && r.height > 0 && x > 0 && x < innerWidth &&
        y > 0 && y < innerHeight, id + ' must have an on-screen center');
    return document.elementFromPoint(x, y);
}
function clickHit(id, focus = false) {
    const hit = hitAtCenter(id);
    assert(hit === byId(id), id + ' intercepted by ' + (hit && hit.outerHTML));
    const previous = hits[id] || 0;
    // Calling the intended element's .click() alone would bypass the shield.
    // Resolve the actual hit first, then activate/focus that hit, never the
    // expected element. These synthetic events do not claim trusted OS input.
    hit.click();
    assert(hits[id] === previous + 1, id + ' did not receive the click');
    if (focus) {
        hit.focus();
        assert(document.activeElement === hit, id + ' did not receive focus');
    }
}
function checkLayout(state) {
    const desktop = innerWidth >= 992;
    const collapsed = document.body.classList.contains('sidebar-collapsed');
    const nav = rect(document.querySelector('.navbar'));
    const header = rect(document.querySelector('.app-page-header'));
    const main = rect(byId('app-main'));
    const footer = rect(document.querySelector('.app-footer'));
    assert(getComputedStyle(document.body).display === 'grid', 'actual body grid required');
    assert(near(nav.y, 0) && near(nav.height, 64), 'navbar geometry');
    assert(near(header.y, 64) && near(header.height, 112), 'header geometry');
    assert(near(main.y, 176) && near(main.height, innerHeight - 224), 'main geometry');
    assert(near(footer.y + footer.height, innerHeight), 'footer must remain in viewport');
    const left = desktop && !collapsed ? 248 : 0;
    assert(near(header.x, left) && near(main.x, left) &&
        near(main.width, innerWidth - left), 'sidebar column geometry');
    for (const node of aux) {
        const style = getComputedStyle(node), r = rect(node);
        assert(node.childNodes.length === 0, 'auxiliary node must be empty');
        assert(node.style.position === 'relative' && node.style.zIndex === '1300',
            'preserve simulated TinyMCE inline output');
        assert(style.position === 'absolute', 'production rule must override inline relative');
        assert(style.pointerEvents === 'auto', 'do not blanket-disable auxiliary pointer events');
        assert(near(r.x, 0) && near(r.y, 0) && near(r.width, innerWidth) && near(r.height, 0),
            'empty auxiliary must be out of grid with zero height: ' + JSON.stringify(r));
    }
    report.states.push({state, viewport: [innerWidth, innerHeight], main, aux: aux.map(rect)});
}
function checkControls() {
    for (const id of ['page-title', 'title-input', 'category', 'logout']) {
        clickHit(id, id === 'title-input' || id === 'category');
    }
    byId('title-input').value = 'Hit-tested title';
    assert(byId('title-input').value === 'Hit-tested title', 'input remains editable');
}
function checkAuxiliaryChildren() {
    // Representative TinyMCE menu/dialog children, not the actual editor runtime.
    // No pointer-events override here: inherited blanket disabling must fail.
    const definitions = [
        ['menu-item', 'tox-menu', 160, 16],
        ['dialog-action', 'tox-dialog', 260, 160],
    ];
    definitions.forEach(([id, className, top, left], index) => {
        const panel = document.createElement('div');
        panel.className = className;
        panel.style.cssText = `position:absolute;top:${top}px;left:${left}px;width:170px;height:60px;`;
        const button = document.createElement('button');
        button.id = id;
        button.textContent = id;
        panel.append(button);
        aux[index].append(panel);
        assert(getComputedStyle(button).pointerEvents === 'auto', id + ' pointer events');
        clickHit(id, true);
    });
    aux.forEach(node => node.replaceChildren());
}
try {
    assert(aux.length === 2, 'two simulated body-direct auxiliary nodes required');
    assert(document.querySelectorAll('script[src], link[href]').length === 0, 'fixture must be offline');
    if (negativeControl) {
        assert(innerWidth >= 992, 'negative control requires desktop two-column grid');
        const sheet = byId('production-css').sheet;
        const matches = [...sheet.cssRules].map((rule, index) => ({rule, index}))
            .filter(({rule}) => rule.selectorText === 'body > .tox-tinymce-aux');
        assert(matches.length === 1, 'remove exactly the new production selector, no other CSS');
        sheet.deleteRule(matches[0].index);
        assert(![...sheet.cssRules].some(rule => rule.selectorText === 'body > .tox-tinymce-aux'),
            'negative control must remove the fix');
        const header = rect(document.querySelector('.app-page-header'));
        const main = rect(byId('app-main'));
        [header, main].forEach((row, index) => {
            const r = rect(aux[index]);
            assert(getComputedStyle(aux[index]).position === 'relative', 'pre-fix inline position');
            assert(near(r.x, 0) && near(r.y, row.y) && near(r.width, innerWidth) &&
                near(r.height, row.height) && r.height > 0,
                'auto-placed shield must stretch across its row: ' + JSON.stringify(r));
        });
        for (const [id, shield] of [['page-title', aux[0]], ['title-input', aux[1]], ['category', aux[1]]]) {
            assert(hitAtCenter(id) === shield, id + ' must be intercepted before fix');
            clickHitShield(id, shield);
        }
        assert(document.activeElement !== byId('title-input'), 'shielded input must not gain focus');
        clickHit('logout'); // Navbar is outside the two shielded grid rows.
        report.states.push({state: 'intercepted', aux: aux.map(rect)});
    } else {
        checkLayout('collapsed');
        checkControls();
        checkAuxiliaryChildren();
        const desktop = innerWidth >= 992;
        const toggle = desktop ? 'sidebar-toggle' : 'mobile-toggle';
        clickHit(toggle);
        assert(byId(toggle).getAttribute('aria-expanded') === 'true', 'sidebar opened');
        const sidebar = rect(byId('app-sidebar'));
        assert(near(sidebar.x, 0) && near(sidebar.width, 248), 'open sidebar geometry');
        clickHit('sidebar-link');
        if (desktop) {
            checkLayout('expanded');
            checkControls();
            checkAuxiliaryChildren();
        } else {
            assert(byId('app-sidebar').classList.contains('show'), 'mobile offcanvas opened');
            checkLayout('mobile-open');
        }
        clickHit(desktop ? 'sidebar-toggle' : 'sidebar-close');
        assert(byId(toggle).getAttribute('aria-expanded') === 'false', 'sidebar closed');
        if (!desktop) assert(getComputedStyle(byId('app-sidebar')).visibility === 'hidden',
            'mobile offcanvas hidden after close');
        checkLayout('reclosed');
        checkControls();
        checkAuxiliaryChildren();
        checkLayout('children-removed');
    }
    report.status = 'PASS';
} catch (error) {
    report.status = 'FAIL';
    report.error = error.message;
    report.aux = aux.map(rect);
}
function clickHitShield(id, shield) {
    const previous = hits[id] || 0;
    const hit = hitAtCenter(id);
    assert(hit === shield, 'negative hit target changed');
    hit.click();
    assert((hits[id] || 0) === previous, 'shield must prevent intended target click');
}
// Keep diagnostics outside the grid so they cannot affect auto-placement.
const result = document.createElement('pre');
result.id = 'result';
result.hidden = true;
result.textContent = JSON.stringify(report);
document.body.append(result);
</script></body></html>
"""


class TinyMCEAuxLayoutBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = [
            shutil.which("chromium"),
            shutil.which("google-chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        cls.browser = next((path for path in candidates if path and os.path.isfile(path)), None)
        if cls.browser is None:
            raise unittest.SkipTest("Chrome/Edge/Chromium is required for CSS browser regression tests")
        template = (Path(__file__).resolve().parent.parent / "templates/base.html").read_text(
            encoding="utf-8"
        )
        first_style = re.search(r"<style\b[^>]*>(.*?)</style>", template, re.DOTALL)
        if first_style is None:
            raise AssertionError("base template must contain its production style block")
        cls.base_css = first_style.group(1)

    def run_fixture(self, width, height, *, negative=False):
        fixture = FIXTURE.replace("__BASE_CSS__", self.base_css).replace(
            "__NEGATIVE__", "true" if negative else "false"
        )
        with tempfile.TemporaryDirectory(prefix="nexus-tinymce-aux-") as directory:
            page = Path(directory) / "aux-layout.html"
            page.write_text(fixture, encoding="utf-8")
            completed = subprocess.run(
                [
                    self.browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-first-run",
                    "--disable-background-networking",
                    "--force-device-scale-factor=1",
                    "--hide-scrollbars",
                    "--run-all-compositor-stages-before-draw",
                    f"--window-size={width},{height}",
                    f"--user-data-dir={directory}/profile",
                    "--virtual-time-budget=1000",
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
        match = re.search(r'<pre id="result"[^>]*>(.*?)</pre>', completed.stdout, re.DOTALL)
        self.assertIsNotNone(match, completed.stdout[-3000:] + completed.stderr[-2000:])
        report = json.loads(html.unescape(match.group(1)))
        self.assertEqual(report.get("status"), "PASS", json.dumps(report, indent=2))
        self.assertEqual(report["mode"], "negative" if negative else "fixed")
        return report

    def test_auxiliary_layout_and_hit_targets_desktop_and_mobile(self):
        for width, height in [(1280, 800), (1024, 768), (390, 844), (768, 900)]:
            with self.subTest(window=(width, height)):
                report = self.run_fixture(width, height)
                viewport = report["states"][0]["viewport"]
                self.assertEqual(viewport[0] >= 992, width >= 992)
                self.assertEqual(
                    [state["state"] for state in report["states"]],
                    ["collapsed", "expanded" if width >= 992 else "mobile-open",
                     "reclosed", "children-removed"],
                )

    def test_negative_control_without_exact_production_rule_intercepts_desktop(self):
        report = self.run_fixture(1280, 800, negative=True)
        self.assertEqual(report["states"][0]["state"], "intercepted")


if __name__ == "__main__":
    unittest.main()