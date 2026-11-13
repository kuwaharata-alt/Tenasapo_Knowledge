"""All common page headers must always show a description below the title."""

from html.parser import HTMLParser
from pathlib import Path
import re
from types import SimpleNamespace

from django.template import Context, Template
from django.test import SimpleTestCase


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
    def test_all_common_headers_have_one_description_in_create_and_edit_modes(self):
        directory = Path(__file__).parent / 'templates' / 'tenasapo_knowledge'
        checked = 0
        for path in sorted(directory.glob('*.html')):
            source = path.read_text(encoding='utf-8')
            match = re.search(r'{% block page_header %}(.*?){% endblock %}', source, re.S)
            if not match or 'page-title-panel' not in match.group(1):
                continue
            checked += 1
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
                        }
                        for name in ('article', 'tip', 'feature', 'category', 'history', 'document'):
                            context[name] = item if editing else None
                        html = Template(match.group(1)).render(Context(context))
                        parser = HeaderParser()
                        parser.feed(html)
                        self.assertEqual(len(parser.descriptions), 1)
                        self.assertTrue(parser.descriptions[0].strip())
                        if path.name == 'manual_detail.html' and has_description:
                            self.assertIn('登録済み説明', html)
                        if path.name in ('user_form.html', 'user_list.html'):
                            self.assertIn('管理者アカウントのみ利用できます。', html)
        self.assertEqual(checked, 32)  # Includes the Chat schedule settings page.