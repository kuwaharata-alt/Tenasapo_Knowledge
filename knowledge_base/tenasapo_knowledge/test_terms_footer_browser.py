"""Offline native-grid fixtures using the real base CSS and footer (no CDN)."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class TermsFooterBrowserTests(SimpleTestCase):
    def test_footer_stays_visible_and_split_results_scroll_independently(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        base = render_to_string('base.html')
        css = re.search(r'<style>(.*?)</style>', base, re.S).group(1)
        footer = re.search(r'<footer class="app-footer">.*?</footer>', base, re.S).group(0)
        for width in (1280, 540, 320):
            for split in (False, True):
                with self.subTest(width=width, split=split):
                    body_class = 'knowledge-split-page has-sidebar sidebar-collapsed' if split else ''
                    long_text = '<div style="height:2400px">Offline content</div>'
                    content = (f'<div class="knowledge-split-row"><aside class="category-sidebar-col">Categories</aside>'
                               f'<section class="category-content-col"><div class="knowledge-results">{long_text}</div>'
                               '</section></div>') if split else long_text
                    fixture = f'''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>{css} * {{ box-sizing: border-box; }}</style></head>
<body class="{body_class}"><nav class="navbar">Nexus</nav>
<section class="app-page-header">Page title</section>
<main class="app-main"><div class="app-content">{content}</div></main>{footer}
<output id="result" style="position:fixed;top:0;right:0">PENDING</output>
<script>
const assert = (ok, message) => {{ if (!ok) throw new Error(message); }};
try {{
 const footer = document.querySelector('.app-footer');
 const link = footer.querySelector('a');
 const main = document.querySelector('.app-main');
 const before = footer.getBoundingClientRect();
 assert(link.textContent === '利用規約' && link.getAttribute('href') === '/terms/', 'footer link');
 assert(Math.abs(before.bottom - innerHeight) < 2, 'footer at viewport bottom');
 assert(before.top >= 0 && before.right <= innerWidth + 1, 'footer within viewport');
 const linkRect = link.getBoundingClientRect();
 assert(linkRect.right <= innerWidth && Math.abs(linkRect.right - (before.right - 16)) < 2, 'terms rightmost');
 assert(linkRect.top >= before.top && linkRect.bottom <= before.bottom, 'link contained on mobile');
 assert(footer.lastElementChild === link && footer.textContent.indexOf('COPYRIGHT') < footer.textContent.indexOf('利用規約'), 'copyright then terms');
 assert(main.getBoundingClientRect().bottom <= before.top + 1, 'content cannot overlap footer');
 const scroller = document.querySelector('.knowledge-results') || main;
 scroller.scrollTop = 500;
 assert(scroller.scrollTop > 0, 'content scrolls');
 assert(window.scrollY === 0, 'window stays fixed');
 assert(Math.abs(footer.getBoundingClientRect().top - before.top) < 1, 'footer stays fixed');
 if (document.querySelector('.knowledge-results')) assert(main.scrollTop === 0, 'split pane owns scrolling');
 document.getElementById('result').textContent = 'PASS';
}} catch (error) {{ document.getElementById('result').textContent = 'FAIL: ' + error.message; }}
</script></body></html>'''
                    with tempfile.TemporaryDirectory(prefix='nexus-terms-footer-') as directory:
                        page = Path(directory) / 'footer.html'
                        page.write_text(fixture, encoding='utf-8')
                        result = subprocess.run([
                            browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                            '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND',
                            f'--user-data-dir={directory}/profile', f'--window-size={width},800',
                            '--dump-dom', page.as_uri(),
                        ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
                        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                        outcome = re.search(r'<output id="result"[^>]*>(.*?)</output>', result.stdout, re.S)
                        self.assertIsNotNone(outcome, result.stdout[-2000:])
                        self.assertEqual(outcome.group(1), 'PASS')


class TermsAcceptanceBrowserTests(SimpleTestCase):
    def test_nested_markers_wrap_align_and_fit_at_320_and_1280_pixels(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        long_text = '折り返した本文の開始位置をそろえて読みやすくします。' * 90
        description = '\n'.join([
            '第1条（番号と箇条書き）', '1. ' + long_text, '・' + long_text,
            '(1) ' + long_text, '・' + long_text, '（２）' + long_text, '･' + long_text,
            '10. ' + long_text, '100. ' + long_text, '１．' + long_text,
            '（１）' + long_text, '・' + long_text,
        ])
        content = render_to_string('tenasapo_knowledge/includes/terms_content.html', {
            'terms': {'description': description},
        })
        css = render_to_string('tenasapo_knowledge/includes/terms_styles.html')
        for width in (320, 1280):
            for font_size in (16, 20):
                with self.subTest(width=width, font_size=font_size), tempfile.TemporaryDirectory(
                        prefix='nexus-terms-markers-') as directory:
                    terms_page = Path(directory) / 'terms.html'
                    terms_page.write_text(f'''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>* {{ box-sizing:border-box; }} html {{ font-size:{font_size}px; }}
body {{ margin:0; }} .terms-document {{ margin:0; }}</style>{css}</head>
<body><article class="terms-document">{content}</article></body></html>''', encoding='utf-8')
                    fixture = '''<!doctype html><meta charset="utf-8">
<iframe id="page" style="border:0;width:WIDTHpx;height:800px"></iframe>
<output id="result">PENDING</output><script>
const frame = document.getElementById('page');
const assert = (ok, message) => { if (!ok) throw new Error(message); };
frame.onload = () => {
 try {
  const doc = frame.contentDocument, win = frame.contentWindow;
  const box = el => el.getBoundingClientRect();
  const style = el => win.getComputedStyle(el);
  const textBox = el => {
   const range = doc.createRange(); range.selectNodeContents(el); return range.getBoundingClientRect();
  };
  assert(win.innerWidth === WIDTH, 'exact iframe viewport');
  const heading = doc.querySelector('.terms-article-heading');
  const primary = [...doc.querySelector('.terms-text-body > .terms-numbered-list').children];
  assert(primary.length === 4, 'four primary items including 10/100/fullwidth');
  assert(primary.map(el => el.firstElementChild.textContent).join('|') === '1.|10.|100.|１．', 'markers unchanged');
  assert(doc.querySelectorAll('.terms-parenthetical-list').length === 2, 'parenthetical groups');
  assert(doc.querySelectorAll('.terms-bullet-list').length === 4, 'bullet groups');
  const headingX = textBox(heading).left;
  for (const item of primary) {
   assert(Math.abs(box(item.firstElementChild).left - headingX) < 1, 'primary marker starts at heading text');
  }
  const firstText = primary[0].querySelector('.terms-item-text');
  const em = parseFloat(style(firstText).fontSize);
  const inset = box(firstText).left - headingX;
  assert(inset >= 1.7 * em && inset <= 1.8 * em, 'compact single-digit body inset');
  assert(box(primary[2].querySelector('.terms-item-text')).left > box(firstText).left,
      '100 marker grows naturally without overlap');
  for (const item of doc.querySelectorAll('.terms-numbered-item')) {
   const marker = item.firstElementChild, text = marker.nextElementSibling;
   assert(style(marker).color === 'rgb(0, 0, 0)', 'black marker');
   assert(box(text).left - box(marker).right >= .74 * em, 'clear marker-body gap');
   const range = doc.createRange(); range.selectNodeContents(text);
   const lines = [...range.getClientRects()];
   assert(lines.length > 1, 'long item really wraps');
   assert(lines.every(line => Math.abs(line.left - box(text).left) < 1), 'wrapped lines align to body');
   assert(item.scrollWidth <= item.clientWidth + 1, 'item has no overflow');
   assert(box(text).width >= 60, 'nested body still has usable width');
  }
  for (const list of doc.querySelectorAll('.terms-item-children > .terms-numbered-list')) {
   const parent = list.parentElement.parentElement;
   const parentText = parent.querySelector(':scope > .terms-item-text');
   const extraInset = list.classList.contains('terms-parenthetical-list') ? em : 0;
   assert(Math.abs(box(list.firstElementChild.firstElementChild).left - box(parentText).left - extraInset) < 1,
       'parenthetical marker indented one em; bullet stays at parent body');
   assert(box(list).top >= box(parentText).bottom, 'nested list below parent body');
   assert(list.parentElement.closest('li') === parent, 'nested list remains in parent li');
  }
  assert(doc.documentElement.scrollWidth <= win.innerWidth + 1, 'no horizontal page overflow');
  document.getElementById('result').textContent = 'PASS';
 } catch(error) { document.getElementById('result').textContent = 'FAIL: ' + error.message; }
}; frame.src = 'terms.html';
</script>'''.replace('WIDTH', str(width))
                    page = Path(directory) / 'layout.html'
                    page.write_text(fixture, encoding='utf-8')
                    result = subprocess.run([
                        browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                        '--allow-file-access-from-files', '--disable-background-networking',
                        '--host-resolver-rules=MAP * ~NOTFOUND', f'--user-data-dir={directory}/profile',
                        '--virtual-time-budget=3000', '--dump-dom', page.as_uri(),
                    ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    outcome = re.search(r'<output id="result"[^>]*>(.*?)</output>', result.stdout, re.S)
                    self.assertIsNotNone(outcome, result.stdout[-2000:])
                    self.assertEqual(outcome.group(1), 'PASS')

    def test_saved_article_titles_have_visible_orange_lines_in_full_templates(self):
        """Reproduce the local storage shape: headings in titles, not bodies."""
        from .terms import DEFAULT_TERMS_TITLE

        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        articles = [{'pk': index, 'title': f'第{index}条（表示検証）',
                     'description': '\n'.join(f'{item}. ' + '表示検証の本文です。' * 12 for item in range(1, 10))
                                    + '\n第十条（本文内の見出し）\n1. 本文内の番号の表示検証です。'}
                    for index in range(1, 4)]
        primary = {'pk': 10, 'title': DEFAULT_TERMS_TITLE, 'description': '検証用の前文です。', 'is_header_title': True}
        for page_name, editable in (('terms_detail.html', False), ('terms_detail.html', True),
                                    ('terms_accept.html', False)):
            for include_primary in (True, False):
                rendered = render_to_string('tenasapo_knowledge/' + page_name, {
                    'terms_list': [primary, *articles] if include_primary else articles,
                    'terms_registered': True, 'csrf_token': 'offline-csrf-token',
                    'terms_token': 'offline-token', 'default_terms_title': DEFAULT_TERMS_TITLE,
                    'can_edit_terms': editable,
                })
                rendered = rendered.replace('<head>', '''<head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
*, *::before, *::after { box-sizing: border-box; }
body { margin:0; line-height:1.5; }
.container { width:100%; padding-inline:12px; margin-inline:auto; }
.py-4 { padding-block:1.5rem !important; }
.mb-3 { margin-bottom:1rem !important; }
.btn { display:inline-block; padding:.375rem .75rem; line-height:1.5; border:1px solid; }
.form-check { min-height:1.5rem; padding-left:1.5em; }
.form-check-input { float:left; margin-left:-1.5em; width:1em; height:1em; }
</style>''', 1)
                rendered = re.sub(r'<nav\b.*?</nav>', '<nav class="navbar" style="min-height:56px">Nexus</nav>',
                                  rendered, count=1, flags=re.S)
                rendered = rendered.replace('</head>', '''<style>
body { --tk-primary:teal; } h2 { border-left:1px dashed teal; }
</style></head>''', 1)
                for width, height in ((320, 540), (1280, 800)):
                    with self.subTest(page=page_name, editable=editable, primary=include_primary, width=width), \
                            tempfile.TemporaryDirectory(prefix='nexus-registered-headings-') as directory:
                        terms_page = Path(directory) / 'terms.html'
                        terms_page.write_text(rendered, encoding='utf-8')
                        fixture = '''<!doctype html><meta charset="utf-8">
<iframe id="page" style="border:0;width:WIDTHpx;height:HEIGHTpx"></iframe>
<output id="result">PENDING</output><script>
const frame = document.getElementById('page');
const assert = (ok, message) => { if (!ok) throw new Error(message); };
frame.onload = () => {
 try {
  const doc = frame.contentDocument, win = frame.contentWindow;
    assert(win.innerWidth === WIDTH, 'exact viewport width');
  const pane = doc.querySelector('.terms-scroll');
  assert(doc.querySelectorAll('.terms-scroll').length === 1, 'exactly one pane');
  assert(doc.querySelector('#terms-page-title').textContent === 'PRIMARY_TITLE', 'exact main title');
  assert([...doc.querySelectorAll('h1,h2')].filter(el => el.textContent === 'PRIMARY_TITLE').length === 1,
         'main title only once');
  const headings = [...doc.querySelectorAll('.terms-document-title.terms-article-heading')];
  assert(headings.length === 3, 'all saved article titles including first');
  const controls = [...doc.querySelectorAll('#terms-accept-form, .terms-actions, .terms-toolbar')];
  const before = controls.map(el => el.getBoundingClientRect().top);
  const allHeadings = [...doc.querySelectorAll('.terms-article-heading')];
  assert(allHeadings.length === 6, 'saved titles and in-body headings covered');
  for (const heading of allHeadings) {
   const list = heading.classList.contains('terms-document-title')
    ? heading.closest('article').querySelector('.terms-numbered-list') : heading.nextElementSibling;
   assert(list.matches('.terms-numbered-list'), 'list belongs to this heading');
   const range = doc.createRange(); range.selectNodeContents(heading);
   const textX = range.getBoundingClientRect().left;
   for (const marker of list.querySelectorAll('.terms-number')) {
    assert(Math.abs(marker.getBoundingClientRect().left - textX) < 1,
        'NUMBER start equals heading TEXT start: ' + marker.getBoundingClientRect().left + '/' + textX);
    assert(win.getComputedStyle(marker).color === 'rgb(0, 0, 0)', 'number computed black');
    assert(marker.nextElementSibling.getBoundingClientRect().left > marker.getBoundingClientRect().right,
        'item text remains right of number');
   }
  }
  const edits = [...doc.querySelectorAll('.terms-section-edit')];
  assert(edits.length === (EDITABLE ? doc.querySelectorAll('article').length : 0), 'edit permission');
    if (!EDITABLE) assert(!doc.querySelector('a[href^="/terms/"][href$="/edit/"]'), 'unprivileged no edit or register');
  for (const edit of edits) {
   const section = edit.closest('article'), row = section.querySelector('.terms-section-heading');
   pane.scrollTop += row.getBoundingClientRect().top - pane.getBoundingClientRect().top - 8;
   const r = edit.getBoundingClientRect(), h = row.getBoundingClientRect(), s = section.getBoundingClientRect();
   assert(row.contains(edit) && Math.abs(r.top - h.top) < 1 && Math.abs(r.right - h.right) < 1,
       'edit at header top right');
   assert(r.left >= s.left && r.right <= s.right + 1 && r.top >= s.top && r.bottom <= h.bottom + 1,
       'edit contained within own section header');
   const title = row.querySelector('h2');
   if (title) assert(title.getBoundingClientRect().right <= r.left,
            'edit does not cover title or orange border');
   else assert(section.getAttribute('aria-labelledby') === 'terms-page-title', 'hidden primary has own edit row');
   assert(doc.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === edit,
       'edit visible and clickable');
  }
  if (EDITABLE) {
   const register = doc.querySelector('.terms-register'), toolbar = doc.querySelector('.terms-toolbar');
   assert(Math.abs(register.getBoundingClientRect().right - toolbar.getBoundingClientRect().right) < 1,
       'register at toolbar right');
  }
  for (const [index, heading] of headings.entries()) {
   pane.scrollTop += heading.getBoundingClientRect().top - pane.getBoundingClientRect().top - 8;
   const style = win.getComputedStyle(heading), rect = heading.getBoundingClientRect();
   const surface = pane.getBoundingClientRect();
   assert(heading.textContent === '第' + (index + 1) + '条（表示検証）', 'exact section text');
   assert(style.borderLeftWidth === '4px' && style.borderLeftStyle === 'solid' &&
          style.borderLeftColor === 'rgb(237, 125, 34)', 'visible orange 4px solid border');
   assert(rect.width > 4 && rect.height > 16 && style.display !== 'none' &&
          style.visibility === 'visible' && Number(style.opacity) > 0, 'nonzero visible heading');
   assert(rect.top >= surface.top && rect.bottom <= surface.bottom && rect.left >= 0 &&
          rect.right <= win.innerWidth, 'orange line inside visible scroll area');
   assert(doc.elementFromPoint(rect.left + 2, rect.top + rect.height / 2) === heading,
          'actual orange border is painted and unobscured');
  }
  const scrollers = [...doc.querySelectorAll('body *')].filter(el =>
      ['auto','scroll'].includes(win.getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 1);
  assert(scrollers.length === 1 && scrollers[0] === pane, 'only body scrolls');
  controls.forEach((el, index) => {
   const rect = el.getBoundingClientRect();
   assert(!pane.contains(el) && Math.abs(rect.top - before[index]) < 1 &&
          rect.top >= 0 && rect.bottom <= win.innerHeight, 'controls stationary outside pane');
  });
  assert(doc.documentElement.scrollWidth <= win.innerWidth + 1, 'no horizontal overflow');
  assert(doc.documentElement.scrollHeight <= win.innerHeight + 1 && win.scrollY === 0, 'no outer scroll');
  document.getElementById('result').textContent = 'PASS rgb(237, 125, 34) 4px solid';
 } catch(error) { document.getElementById('result').textContent = 'FAIL: ' + error.message; }
}; frame.src = 'terms.html';
</script>'''.replace('WIDTH', str(width)).replace('HEIGHT', str(height)).replace('PRIMARY_TITLE', DEFAULT_TERMS_TITLE).replace('EDITABLE', str(editable).lower())
                        page = Path(directory) / 'layout.html'
                        page.write_text(fixture, encoding='utf-8')
                        result = subprocess.run([
                            browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                            '--allow-file-access-from-files', '--disable-background-networking',
                            '--host-resolver-rules=MAP * ~NOTFOUND', f'--user-data-dir={directory}/profile',
                            '--virtual-time-budget=3000', '--dump-dom', page.as_uri(),
                        ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
                        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                        outcome = re.search(r'<output id="result"[^>]*>(.*?)</output>', result.stdout, re.S)
                        self.assertIsNotNone(outcome, result.stdout[-2000:])
                        self.assertEqual(outcome.group(1), 'PASS rgb(237, 125, 34) 4px solid')

    def test_shared_terms_layout_at_real_320_and_1280_pixel_viewports(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        title = 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件'
        from .test_terms import TERMS_PASTE_SAMPLE

        body = TERMS_PASTE_SAMPLE + '\n\n通常の段落です。\n改行も保持します。\n' + 'x' * 300
        cases = [(page, count, '') for page in ('terms_detail.html', 'terms_accept.html')
             for count in (0, 1, 8)]
        cases.append(('terms_accept.html', 8,
                  '利用規約が更新されたか、確認情報が無効です。内容を再確認して同意してください。'))
        for page_name, count, consent_error in cases:
            documents = [{'pk': number, 'title': title if number == 1 else '追加規約' + '長いタイトル' * 24,
                          'description': body, 'is_header_title': number == 1} for number in range(1, count + 1)]
            rendered = render_to_string('tenasapo_knowledge/' + page_name, {
                'terms_registered': bool(count), 'terms_token': 'offline-token',
                'csrf_token': 'offline-csrf-token', 'terms': documents[0] if count else None,
                'default_terms_title': title, 'terms_list': documents,
                'can_edit_terms': page_name == 'terms_detail.html',
                'consent_error': consent_error,
            })
            # Minimal offline Bootstrap primitives (reset, spacing, controls).
            # All viewport/grid and terms rules come from the actual templates.
            # No CDN, fonts, DB, server or network are needed.
            rendered = rendered.replace('<head>', '''<head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; line-height: 1.5; } h2, p { margin-top: 0; margin-bottom: 1rem; }
.container { width: 100%; padding-left: 12px; padding-right: 12px; margin: auto; }
.py-4 { padding-top: 1.5rem !important; padding-bottom: 1.5rem !important; }
.mb-3 { margin-bottom: 1rem !important; } .d-flex { display: flex; }
.align-items-center { align-items: center; } .gap-2 { gap: .5rem; }
.btn { display: inline-block; padding: .375rem .75rem; font-size: 1rem;
       line-height: 1.5; border: 1px solid; border-radius: .375rem; }
.btn-sm { font-size: .875rem; padding: .25rem .5rem; }
.form-check { min-height: 1.5rem; padding-left: 1.5em; margin-bottom: .125rem; }
.form-check-input { float: left; margin-left: -1.5em; width: 1em; height: 1em; }
.alert { padding: 1rem; margin-bottom: 1rem; border: 1px solid; }
</style>''', 1)
            # The CDN-dependent navbar is not this fixture's subject. Without
            # Bootstrap its nowrap brand creates a false min-content overflow.
            rendered = re.sub(r'<nav\b.*?</nav>', '<nav class="navbar" style="min-height:56px">Nexus</nav>', rendered, count=1, flags=re.S)
            if page_name == 'terms_accept.html':
                # The consent view is customer-only; exercise its actual grid columns.
                rendered = rendered.replace('class="terms-page"',
                                            'class="has-sidebar sidebar-collapsed terms-page"', 1)
            for width, height, font_size in ((320, 900, 16), (1280, 900, 16),
                                             (320, 540, 16), (1280, 540, 16),
                                             (1280, 480, 16), (320, 480, 16),
                                             (320, 600, 20), (1280, 600, 20), (320, 360, 16)):
                with self.subTest(page=page_name, count=count, error=bool(consent_error), width=width, height=height,
                                  font_size=font_size), tempfile.TemporaryDirectory(
                        prefix='nexus-terms-layout-') as directory:
                    terms_page = Path(directory) / 'terms.html'
                    terms_page.write_text(rendered.replace('</head>',
                                f'''<style>html {{ font-size: {font_size}px; }}
/* Deliberately change the inherited theme and add a later generic heading
    rule: the actual terms stylesheet must still supply the orange solid line. */
body {{ --tk-primary: #147d78; }}
h2 {{ border-left: 1px dashed teal; }}
</style></head>''', 1), encoding='utf-8')
                    # A sized iframe guarantees the real mobile CSS viewport even
                    # on Chrome versions that clamp the outer window to 500px.
                    fixture = f'''<!doctype html><meta charset="utf-8">
<iframe id="page" style="border:0;width:{width}px;height:{height}px"></iframe>
<output id="result">PENDING</output>
<script>
const frame = document.getElementById('page');
const assert = (ok, message) => {{ if (!ok) throw new Error(message); }};
frame.onload = () => {{
 try {{
  const win = frame.contentWindow, doc = frame.contentDocument;
  assert(win.innerWidth === {width}, 'exact viewport width');
  assert(win.innerHeight === {height}, 'exact viewport height');
  assert(parseFloat(win.getComputedStyle(doc.documentElement).fontSize) === {font_size}, 'root font size');
  const styles = element => win.getComputedStyle(element);
  const main = doc.querySelector('.app-main');
  const footer = doc.querySelector('.app-footer');
  const header = doc.querySelector('.app-page-header');
  const visible = element => {{
   const r = element.getBoundingClientRect();
   return r.top >= -1 && r.bottom <= win.innerHeight + 1 && r.left >= -1 && r.right <= win.innerWidth + 1;
  }};
  assert(visible(header) && visible(footer), 'fixed header/footer visible');
  assert(doc.documentElement.scrollHeight <= win.innerHeight + 1, 'no outer vertical overflow');
    assert(main.scrollHeight <= main.clientHeight + 1,
            'main does not overflow: ' + main.scrollHeight + '/' + main.clientHeight);
  assert(doc.documentElement.scrollWidth <= win.innerWidth + 1, 'no horizontal overflow');
  assert(doc.querySelectorAll('.terms-document-section').length === ({count} ||
      ({'true' if page_name == 'terms_detail.html' else 'false'} ? 1 : 0)), 'document count including empty state');
  const pane = doc.querySelector('.terms-scroll');
    assert(doc.querySelectorAll('.terms-scroll').length === 1, 'one shared pane including empty state');
    assert(doc.querySelector('.page-title-main').textContent === '{title}', 'title preserved');
  assert(!doc.querySelector('.page-title-sub'), 'title header only');
  // Compare the actual cascade with the same common header on a non-terms page.
  const headingStyle = styles(doc.querySelector('.page-title-main'));
  const panelStyle = styles(doc.querySelector('.page-title-panel'));
  const properties = ['fontSize', 'lineHeight', 'fontWeight', 'fontFamily'];
  const headingValues = properties.map(key => headingStyle[key]);
  const panelPadding = ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'].map(key => panelStyle[key]);
  const headerPadding = [styles(header).paddingTop, styles(header).paddingBottom];
  doc.body.classList.remove('terms-page');
  assert(properties.every((key, index) => styles(doc.querySelector('.page-title-main'))[key] === headingValues[index]),
      'same heading size and typography as common pages at every viewport');
  assert(['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'].every((key, index) =>
      styles(doc.querySelector('.page-title-panel'))[key] === panelPadding[index]), 'same common panel padding');
  assert(styles(header).paddingTop === headerPadding[0] && styles(header).paddingBottom === headerPadding[1],
      'same common outer header padding');
  doc.body.classList.add('terms-page');
  if (!{count}) {{
   assert(doc.body.textContent.includes('利用規約はまだ登録されていません。'), 'empty state');
   assert(!doc.querySelector('#terms-accepted'), 'no empty consent');
   assert(visible(doc.querySelector('.terms-toolbar') || doc.querySelector('.terms-actions')), 'empty controls visible');
   document.getElementById('result').textContent = 'PASS'; return;
  }}
  assert(pane.tabIndex === 0 && pane.getAttribute('role') === 'region' &&
      !!pane.getAttribute('aria-label'), 'keyboard accessible region');
    if ({'true' if consent_error else 'false'}) {{
     const error = doc.querySelector('[role="alert"]');
     assert(pane.contains(error) && error.textContent === '{consent_error}', 'full error in shared pane');
    }}
    const sections = [...doc.querySelectorAll('.terms-document-section')];
    assert(doc.querySelector('#terms-page-title').textContent === '{title}', 'exact first saved title');
    assert(!sections[0].querySelector('.terms-document-title'), 'no duplicate first document title');
    assert(sections[0].getAttribute('aria-labelledby') === 'terms-page-title', 'first article uses header label');
    assert(doc.querySelectorAll('.terms-document-title').length === {count} - 1, 'subsequent titles retained');
    sections.slice(1).forEach(section => {{
     const title = section.querySelector('.terms-document-title');
     assert(title.textContent === '追加規約' + '長いタイトル'.repeat(24), 'exact subsequent title');
     assert(section.getAttribute('aria-labelledby') === title.id, 'subsequent article labelled');
    }});
        if ({'true' if page_name == 'terms_detail.html' else 'false'}) {{
         for (const section of sections) {{
          const row = section.querySelector('.terms-section-heading');
          const edit = row.querySelector('.terms-section-edit');
          const r = edit.getBoundingClientRect(), h = row.getBoundingClientRect();
          assert(Math.abs(r.top - h.top) < 1 && Math.abs(r.right - h.right) < 1,
              'edit stays top right with very long or hidden title');
          const title = row.querySelector('.terms-document-title');
          if (title) assert(title.getBoundingClientRect().right <= r.left,
                   'very long title wraps without covering edit');
          assert(row.scrollWidth <= row.clientWidth + 1, 'section header does not overflow');
         }}
        }}
  assert(visible(pane) && pane.clientHeight > 30, 'body has usable remaining height');
  const scrollers = [...doc.querySelectorAll('body *')].filter(el =>
   ['auto', 'scroll'].includes(styles(el).overflowY) && el.scrollHeight > el.clientHeight + 1);
  assert(scrollers.length === 1 && scrollers[0] === pane, 'only shared body scrolls');
  const article = doc.querySelector('.terms-document');
  assert(doc.querySelectorAll('.terms-document').length === 1, 'one shared surface');
  const heading = article.querySelector('h2.terms-article-heading');
  const marker = article.querySelector('.terms-number');
  const text = article.querySelector('.terms-item-text');
  const box = article.getBoundingClientRect();
  assert(styles(article).backgroundColor === 'rgb(250, 248, 243)', 'warm background');
  assert(styles(article).textAlign === 'left', 'left aligned');
  assert(box.left >= 0 && box.right <= win.innerWidth + 1,
      'document fits mobile: ' + box.left + '..' + box.right + '/' + win.innerWidth);
  assert(doc.documentElement.scrollWidth <= win.innerWidth + 1, 'no horizontal overflow');
  assert({width} === 320 || box.width > 900, 'wide desktop text');
  assert(parseFloat(styles(article).paddingLeft) >= 16, 'comfortable padding');
    assert(heading.textContent === '第1条（目的および適用）', 'article heading preserved');
    for (const document of doc.querySelectorAll('.terms-document-section')) {{
     assert(document.querySelectorAll('h2.terms-article-heading').length === 3, 'three article headings');
     const lists = [...document.querySelectorAll('ol.terms-numbered-list')];
     assert(lists.length === 3, 'separate list per article');
     assert(lists.map(list => list.children.length).join(',') === '3,2,4', 'requested item counts');
     assert(lists.every(list => list.querySelector('.terms-number').textContent === '1.'), 'each list restarts');
     for (const item of document.querySelectorAll('.terms-item-text')) {{
        const itemRange = doc.createRange(); itemRange.selectNodeContents(item);
        const itemLines = [...itemRange.getClientRects()];
        assert(itemLines.length > 1, 'every long Japanese item wraps');
        assert(itemLines.every(line => Math.abs(line.left - itemLines[0].left) < 1), 'each item wraps aligned');
     }}
      for (const list of lists) {{
        const range = doc.createRange(); range.selectNodeContents(list.previousElementSibling);
        const headingTextX = range.getBoundingClientRect().left;
        for (const number of list.querySelectorAll('.terms-number')) {{
         assert(Math.abs(number.getBoundingClientRect().left - headingTextX) < 1,
                  'number aligns to in-body heading text at every root font size');
         assert(styles(number).color === 'rgb(0, 0, 0)', 'every number black');
        }}
      }}
    }}
    assert(styles(doc.body).getPropertyValue('--tk-primary').trim() === '#147d78', 'non-orange theme applied');
    for (const articleHeading of article.querySelectorAll('.terms-article-heading')) {{
        assert(styles(articleHeading).borderLeftColor === 'rgb(237, 125, 34)', 'theme-independent orange heading border');
        assert(styles(articleHeading).borderLeftStyle === 'solid', 'solid vertical line');
        assert(parseFloat(styles(articleHeading).borderLeftWidth) === 4, 'thin vertical border');
    }}
    assert(parseFloat(styles(article).paddingTop) <= {font_size} * .75, 'compact body top padding');
    if (!{'true' if page_name == 'terms_detail.html' else 'false'}) {{
        assert(Math.abs(heading.getBoundingClientRect().top - sections[0].getBoundingClientRect().top) < 1,
                    'first article begins immediately without hidden title gap');
    }}
    assert(Math.abs(parseFloat(styles(heading).marginBottom) - {font_size} * .4) < 1,
            'compact heading spacing');
  assert(styles(marker).color === 'rgb(0, 0, 0)' && Number(styles(marker).fontWeight) >= 700,
      'bold black numbers');
  assert(text.getBoundingClientRect().left > marker.getBoundingClientRect().right, 'number gutter');
  const range = doc.createRange(); range.selectNodeContents(text);
  const lines = [...range.getClientRects()];
  assert(lines.length > 1, 'long item wraps');
  assert(lines.every(line => Math.abs(line.left - lines[0].left) < 1), 'wrapped item text aligns');
    assert(Math.abs(parseFloat(styles(text).lineHeight) / parseFloat(styles(text).fontSize) - 1.5) < .01,
            'tighter readable line height');
  assert(article.querySelector('.terms-paragraph').textContent.includes('。\\n改行'), 'newlines retained');
  assert(Math.abs(footer.getBoundingClientRect().bottom - win.innerHeight) < 2, 'footer stays at bottom');
  const headerTop = header.getBoundingClientRect().top;
  const controls = [...doc.querySelectorAll('.terms-toolbar, #terms-accepted, .terms-actions')];
  const controlRects = controls.map(control => control.getBoundingClientRect());
  const contentTop = pane.querySelector('.terms-document-section').getBoundingClientRect().top;
  pane.focus(); pane.scrollTop = pane.scrollHeight;
  assert(pane.scrollTop > 0 && main.scrollTop === 0 && win.scrollY === 0, 'only body moves');
  assert(pane.querySelector('.terms-document-section').getBoundingClientRect().top < contentTop,
      'terms content actually moves');
  controls.forEach((control, index) => {{
   const before = controlRects[index], after = control.getBoundingClientRect();
   assert(visible(control) && Math.abs(before.top - after.top) < 1 &&
       Math.abs(before.right - after.right) < 1, 'controls visible and stationary');
  }});
  const last = pane.querySelector('.terms-document-section:last-child .terms-paragraph:last-child');
  assert(last.getBoundingClientRect().bottom <= pane.getBoundingClientRect().bottom + 1,
      'last document final content reachable');
  assert(Math.abs(header.getBoundingClientRect().top - headerTop) < 1, 'header does not move');
  assert(Math.abs(footer.getBoundingClientRect().bottom - win.innerHeight) < 2, 'footer does not move');
    assert(doc.querySelector('.page-title-main').textContent === '{title}', 'title preserved after scrolling');
  const checkbox = doc.getElementById('terms-accepted');
  if (checkbox) {{
    const actions = doc.querySelector('.terms-actions');
    assert(!pane.contains(checkbox) && !pane.contains(actions), 'controls outside body');
    assert(visible(checkbox) && visible(actions), 'consent controls always visible');
    assert(actions.getBoundingClientRect().bottom <= footer.getBoundingClientRect().top + 1, 'no footer overlap');
    assert(styles(actions).justifyContent === 'flex-end', 'right aligned actions');
    assert(styles(doc.querySelector('#terms-accept-form')).alignSelf === 'flex-end', 'right aligned checkbox');
    assert(Math.abs(actions.lastElementChild.getBoundingClientRect().right -
        actions.getBoundingClientRect().right) < 1, 'buttons at right edge');
   assert(checkbox.required && !checkbox.checked, 'consent remains explicit');
   assert(doc.querySelector('button[form="terms-accept-form"]').textContent === '次へ', 'next preserved');
   assert(doc.querySelector('#terms-back-form button').textContent === '戻る', 'back preserved');
    const form = doc.querySelector('#terms-accept-form'); let submitted = 0;
    form.addEventListener('submit', event => {{ event.preventDefault(); submitted++; }});
    const next = doc.querySelector('button[form="terms-accept-form"]');
    next.click(); assert(submitted === 0, 'unchecked submit blocked');
    checkbox.click(); next.click(); assert(submitted === 1, 'check and submit');
  }} else {{
    assert(visible(doc.querySelector('.terms-toolbar')), 'public toolbar visible');
    assert(doc.querySelectorAll('.terms-document-section a').length === {count}, 'per-document edit links');
  }}
  document.getElementById('result').textContent = 'PASS';
 }} catch (error) {{ document.getElementById('result').textContent = 'FAIL: ' + error.message; }}
}};
frame.src = 'terms.html';
</script>'''
                    page = Path(directory) / 'layout.html'
                    page.write_text(fixture, encoding='utf-8')
                    result = subprocess.run([
                        browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                        '--allow-file-access-from-files', '--disable-background-networking',
                        '--host-resolver-rules=MAP * ~NOTFOUND', f'--user-data-dir={directory}/profile',
                        '--virtual-time-budget=3000', f'--window-size={width},900', '--dump-dom', page.as_uri(),
                    ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    outcome = re.search(r'<output id="result"[^>]*>(.*?)</output>', result.stdout, re.S)
                    self.assertIsNotNone(outcome, result.stdout[-2000:])
                    self.assertEqual(outcome.group(1), 'PASS')

    def test_native_checkbox_validation_and_independent_back_form(self):
        candidates = [shutil.which('chromium'), shutil.which('google-chrome'),
                      r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        browser = next((path for path in candidates if path and Path(path).is_file()), None)
        if not browser:
            self.skipTest('Chrome/Edge/Chromium is required')
        rendered = render_to_string('tenasapo_knowledge/terms_accept.html', {
            'terms_registered': True, 'terms_token': 'offline-version-token',
            'csrf_token': 'offline-csrf-token',
            'terms': {'title': 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件',
                      'description': 'オフラインの表示検証用本文'},
            'terms_list': [{'pk': 1, 'title': '規約1', 'description': '本文1'},
                           {'pk': 2, 'title': '規約2', 'description': '本文2'}],
        })
        controls = re.search(r'<form method="post" action="/terms/accept/".*?</form>\s*</div>', rendered, re.S)
        self.assertIsNotNone(controls)
        fixture = '''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'">
</head><body>''' + controls.group(0) + '''<output id="result">PENDING</output>
<script>
const assert = (ok, message) => { if (!ok) throw new Error(message); };
try {
 const consent = document.getElementById('terms-accept-form');
 const back = document.getElementById('terms-back-form');
 const checkbox = document.getElementById('terms-accepted');
 const next = document.querySelector('button[form="terms-accept-form"]');
 const previous = back.querySelector('button');
 let accepted = 0, cancelled = 0;
 consent.addEventListener('submit', event => {
  event.preventDefault(); accepted++;
  const data = new FormData(consent);
  assert(data.get('terms_accepted') === 'on', 'explicit checked value');
  assert(data.get('terms_token') === 'offline-version-token', 'version preserved');
  assert(data.get('csrfmiddlewaretoken') === 'offline-csrf-token', 'consent CSRF');
 });
 back.addEventListener('submit', event => {
  event.preventDefault(); cancelled++;
  const data = new FormData(back);
  assert(back.getAttribute('method') === 'post', 'logout is POST');
  assert(back.getAttribute('action') === '/accounts/logout/', 'logout destination');
  assert(data.get('next') === '/accounts/login/', 'back goes to login');
  assert(data.get('csrfmiddlewaretoken') === 'offline-csrf-token', 'back CSRF');
  assert(!data.has('terms_accepted') && !data.has('terms_token'), 'back cannot consent');
 });
 assert(checkbox.labels[0].textContent === '利用規約に同意する', 'exact checkbox label');
 assert(next.textContent === '次へ' && previous.textContent === '戻る', 'button labels');
 assert(checkbox.required && !checkbox.checked, 'unchecked required initial state');
 assert(!consent.checkValidity(), 'native validation blocks unchecked consent');
 next.click(); assert(accepted === 0, 'unchecked next blocked');
 previous.click(); assert(cancelled === 1 && accepted === 0, 'unchecked back independent');
 checkbox.click(); assert(consent.checkValidity(), 'checked form valid');
 next.click(); assert(accepted === 1, 'checked next submits');
 previous.click(); assert(cancelled === 2 && accepted === 1, 'checked back cannot consent');
 checkbox.click(); next.click(); assert(accepted === 1, 'unchecking blocks again');
 document.getElementById('result').textContent = 'PASS';
} catch (error) { document.getElementById('result').textContent = 'FAIL: ' + error.message; }
</script></body></html>'''
        with tempfile.TemporaryDirectory(prefix='nexus-terms-consent-') as directory:
            page = Path(directory) / 'consent.html'
            page.write_text(fixture, encoding='utf-8')
            result = subprocess.run([
                browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
                '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND',
                f'--user-data-dir={directory}/profile', '--dump-dom', page.as_uri(),
            ], capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('<output id="result">PASS</output>', result.stdout)