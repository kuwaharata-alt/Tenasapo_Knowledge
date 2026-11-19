"""Common descriptions and the compact, title-only terms header contract."""

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from django.template import Context
from django.template.base import TextNode
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import SimpleTestCase

from .terms import DEFAULT_TERMS_TITLE


EXPECTED_COMMON_HEADERS = {
    'article_detail.html', 'article_form.html', 'article_list.html',
    'article_management.html', 'category_form.html', 'chat_schedule_settings.html',
    'convenience_form.html', 'convenience_list.html', 'csv_import.html',
    'customer_application_detail.html', 'customer_application_list.html',
    'first_login_registration.html', 'home.html', 'knowledge_input.html',
    'login_history_list.html', 'manual_detail.html', 'manual_form.html',
    'manual_list.html', 'posting_quota_settings.html', 'project_document_form.html',
    'project_document_list.html', 'review_list.html', 'revision_history_form.html',
    'revision_history_list.html', 'summary.html', 'tag_form.html',
    'terms_accept.html', 'terms_detail.html', 'tips_detail.html', 'tips_form.html',
    'tips_list.html', 'user_detail.html', 'user_form.html', 'user_issue_form.html',
    'user_list.html', 'view_history_list.html',
}
# The terms layout intentionally reserves the remaining viewport for its shared
# scrolling document and fixed consent controls, including at 320 x 360 pixels.
# Its existing formatting/browser regressions require NO subtitle on these two
# pages. Keep the exception exact; every other common header still requires one.
TITLE_ONLY_HEADERS = {'terms_accept.html', 'terms_detail.html'}


class HeaderParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.descriptions = []
        self.capture = False
        self.title_seen = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'p' and 'page-title-sub' in attrs.get('class', '').split():
            assert self.title_seen, 'Description must follow the page title'
            self.descriptions.append('')
            self.capture = True

    def handle_endtag(self, tag):
        if tag == 'h1':
            self.title_seen = True
        if tag == 'p':
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.descriptions[-1] += data


class PageHeaderDescriptionsTests(SimpleTestCase):
    def test_common_headers_follow_description_contract_in_create_and_edit_modes(self):
        directory = Path(__file__).parent / 'templates' / 'tenasapo_knowledge'
        checked = set()
        for path in sorted(directory.glob('*.html')):
            template = get_template('tenasapo_knowledge/' + path.name).template
            header = next((node for node in template.nodelist.get_nodes_by_type(BlockNode)
                           if node.name == 'page_header'), None)
            if header is None or not any('page-title-panel' in node.s
                                        for node in header.nodelist.get_nodes_by_type(TextNode)):
                continue
            checked.add(path.name)
            for editing in (False, True):
                for has_description in (False, True):
                    with self.subTest(template=path.name, editing=editing, description=has_description):
                        item = SimpleNamespace(
                            pk=1, id=1, management_code='TEST-001', title='テスト',
                            description='登録済み説明' if has_description else '',
                            is_published=True, is_approved=True, visible_to_customer=True,
                        )
                        context = {
                            'is_edit': editing, 'is_update': editing,
                            'user': SimpleNamespace(is_staff=True, is_superuser=True),
                            'manual': item, 'user_obj': item,
                            'application_backend': 'gas',
                        }
                        for name in ('article', 'tip', 'feature', 'category', 'history', 'document'):
                            context[name] = item if editing else None
                        # Render the compiled block, including nested blocks such
                        # as manual_form's overridable form_description. Regex
                        # extraction stops at the inner endblock and is invalid.
                        render_context = Context(context)
                        with render_context.bind_template(template):
                            html = header.render(render_context)
                        parser = HeaderParser()
                        parser.feed(html)
                        self.assertTrue(parser.title_seen)
                        if path.name in TITLE_ONLY_HEADERS:
                            self.assertEqual(parser.descriptions, [])
                            self.assertIn('id="terms-page-title"', html)
                            self.assertIn(DEFAULT_TERMS_TITLE, html)
                            continue
                        self.assertEqual(len(parser.descriptions), 1)
                        self.assertTrue(parser.descriptions[0].strip())
                        if path.name == 'manual_detail.html' and has_description:
                            self.assertIn('登録済み説明', html)
                        if path.name in ('user_form.html', 'user_list.html'):
                            self.assertIn('管理者アカウントのみ利用できます。', html)
        self.assertEqual(len(EXPECTED_COMMON_HEADERS), 36)
        self.assertSetEqual(checked, EXPECTED_COMMON_HEADERS)