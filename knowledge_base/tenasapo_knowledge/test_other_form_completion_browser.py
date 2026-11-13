"""Non-FAQ completion regressions using the actual shared browser assets.

Fixtures reproduce the eight opted-in templates' field/column contracts, not
their Django views. Only QR selection and document required-field listeners
are simulated; completion and navigation always execute production JavaScript.
No server/database is needed. Browser tests skip when Chrome/Edge is absent.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "tenasapo_knowledge/templates/tenasapo_knowledge"
PROFILES = {
    "tag_form.html": ("generic", ("name",)),
    "revision_history_form.html": ("generic", ("metadata", "category", "title", "content")),
    "manual_form.html": ("generic", ("{{ field.name }}",)),
    "category_form.html": ("category", ("parent", "middle", "child")),
    "first_login_registration.html": (
        "first-login", ("department", "group", "employee", "login", "name")
    ),
    "convenience_form.html": (
        "convenience", ("result", "shortcut", "category", "frequency", "note")
    ),
    "user_form.html": ("user", ("account", "permissions", "other")),
    "project_document_form.html": (
        "document", ("title", "product", "category", "type", "files", "template", "project", "revision")
    ),
}


class OtherFormCompletionIntegrationTests(SimpleTestCase):
    """Source contracts only: these assertions do not claim template rendering."""

    def test_eight_real_templates_opt_in_the_main_form_and_share_navigation(self):
        for filename, (kind, sections) in PROFILES.items():
            with self.subTest(template=filename):
                source = (TEMPLATES / filename).read_text(encoding="utf-8")
                self.assertIn('{% extends "base.html" %}', source)
                for asset in ("navigation", "completion"):
                    self.assertEqual(source.count(
                        "{% include 'includes/knowledge_form_" + asset + "_assets.html' %}"
                    ), 1)
                forms = re.findall(r"<form\b[^>]*>", source)
                opted = [tag for tag in forms if "data-knowledge-completion=" in tag]
                self.assertEqual(len(opted), 1)
                self.assertIn(f'data-knowledge-completion="{kind}"', opted[0])
                self.assertIn('method="post"', opted[0])
                self.assertIn('class="card shadow-sm"', opted[0])
                for key in sections:
                    self.assertIn(f'href="#form-section-{key}"', source)
                    self.assertRegex(source, (
                        r'<[^>]+id="form-section-' + re.escape(key)
                        + r'"[^>]*\bdata-form-section\b[^>]*tabindex="-1"[^>]*>'
                    ))

    def test_real_templates_keep_special_completion_markers(self):
        markers = {
            "tag_form.html": ('data-completion-maxlength="{{ form.name.field.max_length }}"',),
            "revision_history_form.html": (
                'data-completion-maxlength="{{ form.title.field.max_length }}"',
                'data-completion-maxlength="{{ form.update_content.field.max_length }}"',
            ),
            "manual_form.html": (
                "{% elif field.name == 'description' %}",
                "改行はそのまま表示され、HTMLタグは文字として扱われます。",
            ),
            "category_form.html": (
                'id="form-section-parent" data-form-section data-completion-required',
                'data-completion-disallowed="true"',
            ),
            "convenience_form.html": (
                'id="form-section-category" data-form-section data-completion-required',
                'id="form-section-frequency" data-form-section data-completion-required',
                'id="qrBigPane"', 'id="qrMidPane"', 'id="qrSmallPane"',
            ),
            "project_document_form.html": (
                'id="form-section-category" data-form-section data-completion-required',
                'id="form-section-files" data-form-section data-completion-required',
                "data-completion-existing-file", 'accept=".pdf"',
                'accept=".doc,.docx,.xls,.xlsx,.ppt,.pptx"',
                "templateSwitch.addEventListener('change', updateProjectRequired)",
            ),
        }
        for filename, expected in markers.items():
            source = (TEMPLATES / filename).read_text(encoding="utf-8")
            for marker in expected:
                with self.subTest(template=filename, marker=marker):
                    self.assertIn(marker, source)


def _section(key, controls, *, required=False, split=False):
    marker = " data-completion-required" if required else ""
    heading = (
        f'<div id="form-section-{key}" data-form-section{marker} '
        f'tabindex="-1">{key}</div>'
    )
    if split:
        return f'<div class="col-12">{heading}</div><div class="col-12">{controls}</div>'
    return f'<div class="col-12">{heading}{controls}</div>'


class OtherFormCompletionBrowserTests(unittest.TestCase):
    # Deliberately independent: inheriting the FAQ harness would rerun FAQ tests.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        candidates = [shutil.which(name) for name in (
            "chromium", "chromium-browser", "google-chrome", "chrome", "msedge"
        )]
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.extend(str(Path(base) / suffix) for suffix in (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                ))
        cls.browser = next((p for p in candidates if p and Path(p).is_file()), None)
        if cls.browser is None:
            raise unittest.SkipTest("Chrome/Edge/Chromium is required for browser regressions")
        cls.assets = "\n".join(
            (ROOT / "templates/includes" / f"knowledge_form_{name}_assets.html")
            .read_text(encoding="utf-8") for name in ("navigation", "completion")
        )

    def _run_browser(self, kind, sections, checks, *, setup="", extra_assets=""):
        links = "".join(
            f'<a href="#form-section-{key}">{key}</a>' for key, _ in sections
        )
        page = """<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; } html { font-size: 16px; }
#app-main { position: absolute; top: 40px; height: 320px; width: 1180px; overflow: auto; }
.knowledge-form-layout { display: flex; }
.knowledge-form-nav-column { width: 240px; flex-shrink: 0; }
.fields { width: 800px; } .row > .col-12 { min-height: 100px; }
[data-form-section] { min-height: 32px; } .tail { height: 400px; }
#result { position: absolute; top: 800px; }
</style></head><body><main id="app-main"><div class="knowledge-form-layout">
<aside class="knowledge-form-nav-column"><nav class="knowledge-form-nav">
<div class="knowledge-form-nav-links">""" + links + """</div></nav></aside>
<div class="fields"><form class="card shadow-sm" data-knowledge-completion=""" + (
            f'"{kind}"><div class="card-body"><div class="row">'
            + "".join(markup for _, markup in sections)
        ) + """</div></div><button type="submit">Save</button></form>
<div class="tail"></div></div></div></main><button id="outside">Outside</button>
<pre id="result">PENDING</pre><script>
window.completionFrames = [];
window.requestAnimationFrame = callback => window.completionFrames.push(callback);
</script>""" + "<script>" + setup + "</script>" + self.assets + extra_assets
        page += r"""<script>
document.addEventListener('DOMContentLoaded', async () => {
    const form = document.querySelector('form');
    const root = document.getElementById('app-main');
    const field = name => form.elements.namedItem(name);
    const link = key => document.querySelector(`a[href="#form-section-${key}"]`);
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const state = (key, expected) => {
        const icons = link(key).querySelectorAll('.form-completion-icon');
        assert(icons.length === 1, `${key}: one completion icon`);
        const icon = icons[0];
        assert(icon.dataset.state === expected,
            `${key}: expected ${expected}, got ${icon.dataset.state}`);
        assert(icon.getAttribute('role') === 'img', `${key}: accessible role`);
        assert(Boolean(icon.getAttribute('aria-label')), `${key}: accessible label`);
    };
    const settle = async () => {
        for (let tick = 0; tick < 3; tick++) {
            await new Promise(resolve => setTimeout(resolve, 0));
            let count = 0;
            while (window.completionFrames.length) {
                assert(++count < 100, 'RAF queue failed to settle');
                window.completionFrames.shift()(performance.now());
            }
        }
    };
    const change = async (name, value, type = 'input') => {
        const input = field(name);
        if (input.type === 'checkbox') input.checked = value;
        else input.value = value;
        input.dispatchEvent(new Event(type, {bubbles: true}));
        await settle();
    };
    const focus = async control => {
        control.focus({preventScroll: true});
        assert(document.activeElement === control, 'real focus must reach fixture control');
        await settle();
    };
    const jump = async key => { link(key).click(); await settle(); };
    const files = async (name, names) => {
        const transfer = new DataTransfer();
        names.forEach(filename => transfer.items.add(new File(['fixture'], filename)));
        field(name).files = transfer.files;
        field(name).dispatchEvent(new Event('change', {bubbles: true}));
        await settle();
    };
    try {
        await settle();
        assert(document.querySelectorAll('.form-completion-legend').length === 1,
            'one shared completion legend');
""" + checks + r"""
        // Inspect a real cancelable submit event without navigating or saving.
        const submit = new Event('submit', {bubbles: true, cancelable: true});
        assert(form.dispatchEvent(submit) && !submit.defaultPrevented,
            'completion/navigation must never cancel submission');
        await settle();
        assert(document.querySelectorAll('[aria-current="location"]').length === 1,
            'shared navigation retains one accessible current location');
        document.getElementById('result').textContent = 'PASS: other form completion';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + (error.stack || error.message);
    }
});
</script></body></html>"""
        with tempfile.TemporaryDirectory(prefix="nexus-other-completion-") as directory:
            fixture = Path(directory) / "completion.html"
            fixture.write_text(page, encoding="utf-8")
            completed = subprocess.run([
                self.browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--no-first-run", "--disable-background-networking",
                "--run-all-compositor-stages-before-draw", "--force-prefers-reduced-motion",
                "--window-size=1440,1000", f"--user-data-dir={directory}/profile",
                "--virtual-time-budget=5000", "--dump-dom", fixture.as_uri(),
            ], capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            result = re.search(r'<pre id="result">(.*?)</pre>', completed.stdout, re.S)
            self.assertIsNotNone(result, completed.stderr[-2000:])
            self.assertEqual(result.group(1) if result else "", "PASS: other form completion")

    def test_tag_required_name_whitespace_and_explicit_maxlength(self):
        self._run_browser("generic", [("name", _section("name", (
            '<input name="name" required data-completion-maxlength="60">'
        )))], r"""
        state('name', 'empty');
        await focus(field('name'));
        await focus(document.getElementById('outside'));
        state('name', 'warning');
        await change('name', ' \t\u200b '); state('name', 'warning');
        await change('name', 'x'.repeat(60)); state('name', 'complete');
        await change('name', 'x'.repeat(61)); state('name', 'warning');
        await change('name', 'IP'); state('name', 'complete');
        """)

    def test_revision_readonly_metadata_required_category_and_optional_text(self):
        sections = [
            ("metadata", _section("metadata", '<input readonly value="author"><input readonly value="2026/11/11">')),
            ("category", _section("category", '<select name="category" required><option value=""></option><option value="faq">FAQ</option></select>')),
            ("title", _section("title", '<input name="title" data-completion-maxlength="200">')),
            ("content", _section("content", '<textarea name="update_content"></textarea>')),
        ]
        self._run_browser("generic", sections, r"""
        state('metadata', 'complete'); state('category', 'empty');
        await jump('content');
        state('category', 'warning'); state('title', 'empty'); state('content', 'empty');
        await change('category', 'faq', 'change'); state('category', 'complete');
        await change('title', 'x'.repeat(201)); state('title', 'warning');
        await change('title', 'Release'); state('title', 'complete');
        await change('title', ''); state('title', 'empty');
        await change('update_content', 'Updated guide'); state('content', 'complete');
        await change('update_content', ''); state('content', 'empty');
        """)

    def test_manual_required_plaintext_number_zero_and_unpublished_switch(self):
        sections = [
            ("title", _section("title", '<input name="title" required maxlength="200">')),
            ("description", _section("description", '<textarea name="description" rows="18" required></textarea>')),
            ("order", _section("order", '<input name="order" type="number" min="0" value="0" required>')),
            ("is_published", _section("is_published", '<input name="is_published" type="checkbox">')),
        ]
        self._run_browser("generic", sections, r"""
        state('order', 'complete'); state('is_published', 'empty');
        await jump('is_published');
        state('title', 'warning'); state('description', 'warning');
        await change('title', 'Manual'); state('title', 'complete');
        await change('description', '1. Open menu\n2. Read instructions'); state('description', 'complete');
        await change('description', ''); state('description', 'warning');
        await change('order', '-1'); state('order', 'warning');
        await change('order', '0'); state('order', 'complete');
        await change('is_published', true, 'change'); state('is_published', 'complete');
        await change('is_published', false, 'change');
        await focus(document.getElementById('outside')); state('is_published', 'empty');
        """)

    def test_category_existing_or_new_parent_qr_disallowed_and_optional_child(self):
        for qr in (False, True):
            with self.subTest(qr=qr):
                parent, new, middle, child = (
                    ("reference_type", "new_reference_type", "category", "middle_category")
                    if qr else ("parent_name", "new_parent_name", "middle_name", "child_name")
                )
                sections = [
                    ("parent", _section("parent", f'<select name="{parent}"><option value=""></option><option value="existing">Existing</option></select><input name="{new}">', required=True)),
                    ("middle", _section("middle", f'<input name="{middle}" required>')),
                    ("child", _section("child", f'<input name="{child}">')),
                ]
                self._run_browser("category", sections, f"""
                const parentName = '{parent}', newName = '{new}', middleName = '{middle}';
                """ + r"""
                state('parent', 'empty'); state('middle', 'empty'); state('child', 'empty');
                await jump('child'); state('parent', 'warning'); state('middle', 'warning');
                await change(parentName, 'existing', 'change'); state('parent', 'complete');
                await change(parentName, '', 'change'); state('parent', 'warning');
                await change(newName, 'New parent'); state('parent', 'complete');
                if (newName === 'new_reference_type') {
                    field(newName).dataset.completionDisallowed = 'true';
                    await change(parentName, 'existing', 'change');
                    state('parent', 'warning'); // disallowed new value overrides existing selection
                    await change(newName, ''); state('parent', 'complete');
                }
                await change(middleName, 'Middle'); state('middle', 'complete');
                state('child', 'empty');
                """)

    def test_first_login_readonly_department_six_digits_group_and_login(self):
        sections = [
            ("department", _section("department", '<input readonly value="Support">')),
            ("group", _section("group", '<select name="group" required><option value=""></option><option value="A">A</option></select>')),
            ("employee", _section("employee", '<input name="employee_number" required maxlength="6">')),
            ("login", _section("login", '<input name="login_id" required>')),
            ("name", _section("name", '<input name="user_name">')),
        ]
        self._run_browser("first-login", sections, r"""
        state('department', 'complete');
        await jump('name');
        ['group', 'employee', 'login'].forEach(key => state(key, 'warning'));
        state('name', 'empty');
        await change('group', 'A', 'change'); state('group', 'complete');
        for (const value of ['12345', '1234567', 'abcdef', '１２３４５６']) {
            await change('employee_number', value); state('employee', 'warning');
        }
        await change('employee_number', '012345'); state('employee', 'complete');
        await change('login_id', 'worker'); state('login', 'complete');
        await change('user_name', '山田'); state('name', 'complete');
        await change('user_name', ''); state('name', 'empty');
        """)

    def test_convenience_pane_membership_small_branch_and_click_only_hidden_updates(self):
        category = """<div id="qrBigPane"><button type="button" data-value="big">Big</button></div>
<div id="qrMidPane"><button type="button" data-value="mid">Mid</button></div>
<div id="qrSmallPane"><button type="button" data-value="small">Small</button></div>
<input type="hidden" name="reference_type"><input type="hidden" name="category">
<input type="hidden" name="middle_category">"""
        stars = '<input type="hidden" name="usage_frequency" value="3">' + "".join(
            f'<button type="button" data-rating-value="{n}">{n}</button>' for n in range(1, 6)
        )
        sections = [
            ("result", _section("result", '<textarea name="display_text" required></textarea>')),
            ("shortcut", _section("shortcut", '<input name="shortcut_key" required>')),
            ("category", _section("category", category, required=True, split=True)),
            ("frequency", _section("frequency", stars, required=True)),
            ("note", _section("note", '<textarea name="note"></textarea>')),
        ]
        setup = r"""
const qrForm = document.querySelector('form');
window.fixtureChanges = 0;
qrForm.addEventListener('change', () => window.fixtureChanges++);
for (const [pane, name] of [['qrBigPane', 'reference_type'], ['qrMidPane', 'category'],
    ['qrSmallPane', 'middle_category']]) {
    document.getElementById(pane).querySelector('[data-value]').addEventListener('click', event => {
        qrForm.elements.namedItem(name).value = event.currentTarget.dataset.value;
    });
}
document.querySelectorAll('[data-rating-value]').forEach(button => {
    button.addEventListener('click', () => {
        qrForm.elements.namedItem('usage_frequency').value = button.dataset.ratingValue;
    });
});
"""
        self._run_browser("convenience", sections, r"""
        state('category', 'empty'); state('frequency', 'complete');
        await jump('note');
        ['result', 'shortcut', 'category'].forEach(key => state(key, 'warning'));
        state('note', 'empty');
        await change('display_text', 'Open document'); state('result', 'complete');
        await change('shortcut_key', 'Ctrl+O'); state('shortcut', 'complete');
        for (const pane of ['qrBigPane', 'qrMidPane']) {
            document.querySelector(`#${pane} [data-value]`).click(); await settle();
            state('category', 'warning');
        }
        document.querySelector('#qrSmallPane [data-value]').click(); await settle();
        state('category', 'complete');
        assert(window.fixtureChanges === 0, 'QR buttons assign hidden values without change');
        await change('middle_category', 'stale'); state('category', 'warning');
        document.getElementById('qrSmallPane').replaceChildren();
        field('middle_category').value = ''; await settle(); state('category', 'complete');
        await change('reference_type', 'stale'); state('category', 'warning');
        await change('reference_type', 'big'); state('category', 'complete');
        await change('category', 'stale'); state('category', 'warning');
        await change('category', 'mid'); state('category', 'complete');
        for (const value of ['', '0', '6', '10', 'x']) {
            await change('usage_frequency', value); state('frequency', 'warning');
        }
        const changes = window.fixtureChanges;
        for (const button of form.querySelectorAll('[data-rating-value]')) {
            button.click(); await settle(); state('frequency', 'complete');
            assert(field('usage_frequency').value === button.dataset.ratingValue, 'selected star value');
        }
        assert(window.fixtureChanges === changes, 'stars do not dispatch change');
        """, setup=setup)

    def _user_sections(self, *, editing=False, systena=False):
        login = '<input readonly value="worker">' if editing else '<input name="username" required>'
        user_type = '<input type="hidden" name="user_type" value="' + ('systena' if editing and systena else 'customer') + '">'
        if not editing:
            user_type += '<input id="type-switch" type="checkbox" name="user_type" value="systena"' + (' checked' if systena else '') + '>'
        password = '' if editing else '<input name="password" type="password" required>'
        return [
            ("account", _section("account", user_type + login + password
                + '<div id="user-company-field"><input name="company_name" required></div>'
                + '<input name="uid" pattern="[0-9]{6}" maxlength="6">'
                + '<input name="email_addresses" type="email"><input name="department">')),
            ("permissions", _section("permissions", '<input type="hidden" name="role" value="user">'
                + '<input id="role-switch" type="checkbox" name="role" value="admin">')),
            ("other", _section("other", '<textarea name="note"></textarea>')),
        ]

    def test_user_create_and_edit_account_validity_and_same_name_off_defaults(self):
        for editing in (False, True):
            with self.subTest(editing=editing):
                sections = self._user_sections(editing=editing)
                self._run_browser("user", sections, r"""
                state('account', 'empty');
                state('permissions', 'complete');
                await jump('other'); state('account', 'warning'); state('other', 'empty');
                if (field('username')) await change('username', 'worker');
                if (field('password')) await change('password', 'fixture-password');
                await change('company_name', 'Customer'); state('account', 'complete');
                await change('uid', '12345x');
                assert(field('uid').validity.patternMismatch, 'native optional regex really fails');
                state('account', 'warning');
                await change('uid', '012345'); state('account', 'complete');
                await change('uid', ''); state('account', 'complete');
                await change('email_addresses', 'broken'); state('account', 'warning');
                await change('email_addresses', 'worker@example.test'); state('account', 'complete');
                await change('email_addresses', ''); state('account', 'complete');
                if (field('password')) {
                    await change('password', ''); state('account', 'warning');
                    state('permissions', 'complete');
                    await change('password', '   '); state('account', 'warning');
                    state('permissions', 'complete');
                    await change('password', 'fixture-password');
                    state('account', 'complete');
                }
                const role = document.getElementById('role-switch');
                const type = document.getElementById('type-switch');
                for (const checked of [true, false]) {
                    role.click(); if (type) type.click(); await settle();
                    const posted = new FormData(form);
                    assert(posted.getAll('role').at(-1) === (checked ? 'admin' : 'user'), 'last posted role');
                    assert(posted.getAll('user_type').at(-1) === (checked && type ? 'systena' : 'customer'),
                        'last posted user type');
                    state('permissions', 'complete');
                }
                const defaultType = form.querySelector('input[type="hidden"][name="user_type"]');
                defaultType.value = 'invalid';
                defaultType.dispatchEvent(new Event('input', {bubbles: true}));
                await settle(); state('account', 'warning'); state('permissions', 'complete');
                defaultType.value = 'customer';
                defaultType.dispatchEvent(new Event('input', {bubbles: true}));
                await settle(); state('account', 'complete');
                const defaultRole = form.querySelector('input[type="hidden"][name="role"]');
                defaultRole.value = 'invalid';
                defaultRole.dispatchEvent(new Event('input', {bubbles: true}));
                await settle();
                assert(link('permissions').querySelector('.form-completion-icon').dataset.state !== 'complete',
                    'invalid last role prevents permissions completion');
                state('account', 'complete');
                role.click(); await settle(); state('permissions', 'complete');
                await change('note', ''); state('other', 'empty');
                """)

    def test_user_company_actual_assets_initial_systena_and_customer_round_trip(self):
        assets = (ROOT / 'templates/includes/user_form_company_assets.html').read_text(encoding='utf-8')
        self.assertNotIn('{{', assets)
        self.assertNotIn('{%', assets)
        for systena in (False, True):
            with self.subTest(initial_systena=systena):
                self._run_browser('user', self._user_sections(systena=systena), r"""
                const type = document.getElementById('type-switch');
                const company = field('company_name');
                const wrapper = document.getElementById('user-company-field');
                const verify = internal => {
                    assert(!wrapper.hidden && !company.hidden, 'company stays visible for both types');
                    assert(company.type === 'text', 'company is not a hidden input');
                    assert(company.getClientRects().length > 0,
                        'company remains visibly rendered for both types');
                    assert(getComputedStyle(company).visibility === 'visible', 'company is not concealed');
                    assert(!company.disabled, 'company stays enabled for both types');
                    assert(company.readOnly === internal, 'only Systena makes company readonly');
                    assert(company.classList.contains('bg-light') === internal,
                        'only automatic Systena company has grey styling');
                    assert(company.required === !internal, 'customer company is required');
                    const posted = new FormData(form);
                    assert(posted.has('company_name'), 'both types submit company_name');
                    assert(posted.get('company_name') === company.value, 'visible company is submitted');
                    if (internal) {
                        assert(company.value === 'システナ', 'Systena company is filled automatically');
                        assert(posted.get('company_name') === 'システナ', 'automatic company is submitted');
                    }
                    assert(!field('department').required && !field('department').disabled,
                        'department is optional and remains enabled');
                    assert(new FormData(form).get('department') === '', 'blank department is submitted');
                    state('permissions', 'complete');
                };
                verify(type.checked);
                assert(company.value === (type.checked ? 'システナ' : ''),
                    'initial company follows selected type');
                assert(window.companyInputs === 1, 'initial synchronization emits bubbling input');
                await jump('other');
                await change('username', 'worker');
                await change('password', 'fixture-password');
                state('account', type.checked ? 'complete' : 'warning');
                if (type.checked) { type.click(); await settle(); }
                verify(false); state('account', 'warning');
                await change('company_name', 'Customer draft & Co'); state('account', 'complete');
                const inputs = window.companyInputs;
                type.click(); await settle(); verify(true); state('account', 'complete');
                assert(company.value === 'システナ', 'Systena displays automatic company instead of draft');
                assert(window.companyInputs === inputs + 1, 'switch emits input after synchronization');
                await change('password', ''); state('account', 'warning'); state('permissions', 'complete');
                await change('password', 'fixture-password'); state('account', 'complete');
                type.click(); await settle(); verify(false); state('account', 'complete');
                assert(company.value === 'Customer draft & Co', 'customer toggle restores exact company');
                assert(new FormData(form).get('company_name') === 'Customer draft & Co', 'restored company submitted');
                await change('company_name', ''); state('account', 'warning');
                type.click(); await settle(); verify(true); state('account', 'complete');
                type.click(); await settle(); verify(false); state('account', 'warning');
                assert(company.value === '', 'empty customer draft is restored rather than automatic company');
                assert(new FormData(form).get('company_name') === '', 'empty customer company is submitted');
                """, setup=r"""
window.companyInputs = 0;
document.querySelector('form').addEventListener('input', event => {
    if (event.target.name === 'company_name') window.companyInputs++;
});
""", extra_assets=assets)

    def test_user_company_actual_assets_leave_immutable_editing_unchanged(self):
        assets = (ROOT / 'templates/includes/user_form_company_assets.html').read_text(encoding='utf-8')
        for systena in (False, True):
            with self.subTest(systena=systena):
                self._run_browser('user', self._user_sections(editing=True, systena=systena), r"""
                const company = field('company_name');
                assert(!document.getElementById('type-switch'), 'editing has no mutable user type');
                assert(!document.getElementById('user-company-field').hidden, 'edit company remains visible');
                assert(company.required && !company.disabled, 'edit company contract unchanged');
                assert(company.value === 'Existing company', 'edit company value untouched');
                assert(window.companyInputs === 0, 'creation synchronization does not run on edit');
                assert(new FormData(form).get('company_name') === 'Existing company', 'edit company submitted');
                state('account', 'complete'); state('permissions', 'complete');
                """, setup=r"""
window.companyInputs = 0;
const editForm = document.querySelector('form');
editForm.elements.namedItem('company_name').value = 'Existing company';
editForm.addEventListener('input', () => window.companyInputs++);
""", extra_assets=assets)

    def test_user_issue_actual_preview_email_sync_safety_and_disabled_completion(self):
        assets = (ROOT / 'templates/includes/user_issue_preview_assets.html').read_text(encoding='utf-8')
        self.assertNotIn('{{', assets)
        self.assertNotIn('{%', assets)
        for demo, bound in ((False, False), (False, True), (True, False)):
            with self.subTest(demo=demo, bound=bound):
                fixed = ''.join(
                    f'<input id="issue-preview-{key}" class="form-control bg-light" '
                    f'type="text" value="{value}" disabled>'
                    for key, value in (('uid', '900001' if demo else '800001'),
                                       ('login', 'CSDemo900001' if demo else ''),
                                       ('password', 'CSDemo900001' if demo else ''))
                )
                email = '' if demo else (
                    '<input name="email_addresses" type="email" required>'
                    '<div id="issue-preview-email-help" aria-live="polite"></div>'
                    '<input name="display_name" required>'
                )
                permissions = (
                    '<input id="issue-preview-role" type="checkbox" role="switch" disabled>'
                    '<input id="issue-preview-analysis" type="checkbox" role="switch" disabled>'
                    '<input id="issue-preview-demo" type="checkbox" role="switch" disabled'
                    + (' checked' if demo else '') + '>'
                    '<input id="issue-preview-groups" value="fixed group" disabled>'
                )
                sections = [
                    ('account', _section('account', fixed + email + '<input name="company_name" required>')),
                    ('permissions', _section('permissions', permissions)),
                ]
                if not demo:
                    sections.append(('other', _section('other', '<textarea name="note"></textarea>')))
                self._run_browser('user-issue', sections, r"""
                const preview = key => document.getElementById('issue-preview-' + key);
                const demo = !field('email_addresses');
                state('account', 'empty'); state('permissions', 'empty');
                await jump(demo ? 'permissions' : 'other');
                state('account', 'warning'); state('permissions', 'empty');
                const fixed = Array.from(form.querySelectorAll('[id^="issue-preview-"] input, input[id^="issue-preview-"]'));
                fixed.forEach(input => {
                    assert(input.disabled && !input.hasAttribute('name'), 'preview cannot be edited or posted');
                });
                // Even an accidental name cannot make a disabled preview a successful control.
                preview('password').name = 'password';
                assert(!new FormData(form).has('password'), 'disabled password excluded from POST');
                preview('password').removeAttribute('name');
                if (demo) {
                    assert(preview('login').value === 'CSDemo900001', 'demo login unchanged without email');
                    assert(preview('password').value === 'CSDemo900001', 'demo initial password unchanged');
                } else {
                    const helper = document.getElementById('issue-preview-email-help');
                    if (window.boundPreview) {
                        assert(preview('login').value === 'Bound.User@example.test', 'initial bound email synchronized');
                        assert(preview('password').value === 'Bound.User', 'initial bound localpart synchronized');
                        assert(helper.textContent.includes('発行時に確定'), 'initial bound preview helper');
                        await change('email_addresses', '');
                    }
                    assert(preview('login').value === '' && preview('password').value === '', 'blank previews');
                    assert(helper.textContent.includes('メールアドレスを入力すると'), 'blank helper');
                    await change('email_addresses', 'Contract.User@example.test');
                    assert(preview('login').value === 'Contract.User@example.test', 'input updates login');
                    assert(preview('password').value === 'Contract.User', 'input updates localpart');
                    const unsafe = '<img src=x onerror="window.previewXss=true">&local @example.test';
                    await change('email_addresses', unsafe, 'change');
                    assert(preview('login').value === unsafe, 'unsafe email stays literal value');
                    assert(preview('password').value === unsafe.split('@', 1)[0].trim(), 'trimmed localpart');
                    assert(!form.querySelector('img') && !window.previewXss, 'no HTML injection');
                    await change('email_addresses', 'not-an-email');
                    assert(preview('password').value === '', 'no fake password without @');
                    await change('email_addresses', '');
                    assert(preview('login').value === '' && preview('password').value === '', 'clearing clears both');
                    assert(helper.textContent.includes('メールアドレスを入力すると'), 'clearing resets helper');
                    await change('display_name', 'Contract name');
                    await change('email_addresses', 'Contract.User@example.test');
                }
                await change('company_name', 'Customer'); state('account', 'complete');
                state('permissions', 'empty');
                await change('company_name', ''); state('account', 'warning');
                assert(!new FormData(form).has('uid') && !new FormData(form).has('role'), 'fixed settings never posted');
                """, setup=(
                    "window.boundPreview = true; document.querySelector('input[name=email_addresses]').value = '  Bound.User@example.test  ';"
                    if bound else ''
                ), extra_assets=assets)

    def _document_sections(self, updating):
        sections = [
            ("title", _section("title", '<input name="title" required>')),
            ("product", _section("product", '<input name="product_name"><input name="product_version">')),
            ("category", _section("category", '<input type="hidden" name="parent_category"><input type="hidden" name="middle_category"><input type="hidden" name="child_category">', required=True)),
            ("type", _section("type", '<select name="document_type" required><option value="guide" selected>Guide</option></select>')),
            ("files", _section("files", '<input type="file" name="file_office" accept=".doc,.docx,.xls,.xlsx,.ppt,.pptx"><input type="file" name="file_pdf" accept=".pdf"><div id="existing"></div>', required=True)),
            ("template", _section("template", '<input type="checkbox" name="is_template">')),
            ("project", _section("project", '<input name="project_number" required><input name="customer_name" required>')),
        ]
        if updating:
            sections.append(("revision", _section("revision", '<textarea name="revision_note" required></textarea>')))
        return sections

    def test_document_hidden_category_file_alternatives_and_existing_uploads(self):
        self._run_browser("document", self._document_sections(False), r"""
        await jump('project');
        ['title', 'category', 'files'].forEach(key => state(key, 'warning'));
        state('product', 'empty'); state('template', 'empty'); state('type', 'complete');
        await change('title', 'Guide'); state('title', 'complete');
        await change('parent_category', 'Parent'); state('category', 'warning');
        await change('middle_category', 'Middle'); state('category', 'complete');
        await change('middle_category', ''); state('category', 'warning');
        await change('child_category', 'Direct child'); state('category', 'complete');
        await change('parent_category', ''); state('category', 'warning');
        await change('parent_category', 'Parent'); state('category', 'complete');
        for (const extension of ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx']) {
            await files('file_office', [`guide.${extension}`]); state('files', 'complete');
        }
        await files('file_office', []); state('files', 'warning');
        await files('file_pdf', ['guide.PDF']); state('files', 'complete');
        await files('file_office', ['bad.exe']); state('files', 'warning');
        await files('file_office', []); state('files', 'complete');
        await files('file_pdf', ['bad.docx']); state('files', 'warning');
        await files('file_pdf', []); state('files', 'warning');
        const existing = document.getElementById('existing');
        existing.innerHTML = '<a href="saved.docx" data-completion-existing-file>Existing Office</a>';
        await settle(); state('files', 'complete');
        await files('file_pdf', ['bad.txt']); state('files', 'warning');
        await files('file_pdf', []); state('files', 'complete');
        existing.innerHTML = '<a href="saved.pdf" data-completion-existing-file>Existing PDF</a>';
        await settle(); state('files', 'complete');
        existing.replaceChildren(); await settle(); state('files', 'warning');
        """)

    def test_document_template_listener_changes_project_requirement_and_revision(self):
        setup = r"""
const documentForm = document.querySelector('form');
const templateSwitch = documentForm.elements.namedItem('is_template');
const updateProjectRequired = () => {
    for (const name of ['project_number', 'customer_name']) {
        documentForm.elements.namedItem(name).required = !templateSwitch.checked;
    }
};
templateSwitch.addEventListener('change', updateProjectRequired);
updateProjectRequired();
"""
        for updating in (False, True):
            with self.subTest(updating=updating):
                self._run_browser("document", self._document_sections(updating), r"""
                await jump(field('revision_note') ? 'revision' : 'project');
                await focus(document.getElementById('outside'));
                state('project', 'warning');
                await change('is_template', true, 'change');
                assert(!field('project_number').required && !field('customer_name').required,
                    'fixture listener removes native project requirements');
                state('project', 'empty'); state('template', 'complete');
                await change('is_template', false, 'change');
                assert(field('project_number').required && field('customer_name').required,
                    'fixture listener restores native project requirements');
                state('project', 'warning'); state('template', 'empty');
                await change('project_number', 'PRJ-001'); state('project', 'warning');
                await change('customer_name', 'Customer'); state('project', 'complete');
                await change('customer_name', ''); state('project', 'warning');
                if (field('revision_note')) {
                    state('revision', 'warning');
                    await change('revision_note', 'Revised guide'); state('revision', 'complete');
                    await change('revision_note', ''); state('revision', 'warning');
                } else {
                    assert(!link('revision'), 'creation has no revision section');
                }
                """, setup=setup)

    def test_optional_regex_and_file_warn_only_after_navigation_then_clear(self):
        # A small generic fixture isolates optional invalidity from required fields.
        sections = [
            ("code", _section("code", '<input name="code" pattern="[0-9]{6}">')),
            ("attachment", _section("attachment", '<input type="file" name="attachment" accept=".pdf">')),
            ("note", _section("note", '<textarea name="note"></textarea>')),
        ]
        self._run_browser("generic", sections, r"""
        await change('code', 'abcdef'); state('code', 'empty');
        await files('attachment', ['wrong.txt']); state('attachment', 'empty');
        await jump('note'); state('code', 'warning'); state('attachment', 'warning');
        await change('code', '012345'); state('code', 'complete');
        await files('attachment', ['valid.pdf']); state('attachment', 'complete');
        await change('code', ''); state('code', 'empty');
        await files('attachment', []); state('attachment', 'empty'); state('note', 'empty');
        """)