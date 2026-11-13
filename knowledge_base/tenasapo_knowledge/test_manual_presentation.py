"""Lossless plaintext parsing and sidebar-aligned, read-only presentation."""

from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from .manual_defaults import OPERATION_MANUALS
from .manual_presentation import (
    MENU_GROUPS, MENU_ICONS, build_menu_groups, menu_definitions, parse_manual_body,
)
from .models import Manual
from .test_registration_form_layout import RegistrationControlsParser


class ManualBodyParserTests(SimpleTestCase):
    def assert_lossless(self, body):
        blocks = parse_manual_body(body)
        self.assertEqual(''.join(block['source_heading'] + block['body'] for block in blocks), body)
        return blocks

    def test_exact_trimmed_headings_alias_and_all_newlines_preserved(self):
        body = '前置き\r\n\r\n  対象・できること \r\n説明\r\n\r\n操作手順\n1. 操作\n\n注意点\n注意\n'
        blocks = self.assert_lossless(body)
        self.assertEqual([block['heading'] for block in blocks], ['', '内容', '操作手順', '注意点'])
        self.assertEqual([block['body'] for block in blocks],
                         ['前置き\r\n\r\n', '説明\r\n\r\n', '1. 操作\n\n', '注意\n'])

    def test_arbitrary_html_markdown_unknown_and_inline_headings_are_plaintext(self):
        for body in ('', ' \t\n', '内容について\n操作手順: そのまま\n注意点も読む',
                     '# 内容\n**操作手順**\n<h3>注意点</h3>\n未知の見出し\n<script>x</script>&',
                     '対象・できることはこれ\r\n任意の本文\r\n'):
            with self.subTest(body=body):
                self.assertEqual(self.assert_lossless(body),
                                 [{'heading': '', 'source_heading': '', 'body': body}])

    def test_repeated_empty_out_of_order_headings_and_no_final_newline(self):
        blocks = self.assert_lossless('注意点\n内容\n内容\n本文\n操作手順')
        self.assertEqual([block['heading'] for block in blocks], ['注意点', '内容', '内容', '操作手順'])
        self.assertEqual([block['body'] for block in blocks], ['', '', '本文\n', ''])

    def test_unknown_lines_inside_known_block_are_never_discarded(self):
        body = '内容\n未知見出し\n# 操作手順\n<b>内容</b>\n\n注意点: 本文\n'
        blocks = self.assert_lossless(body)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['body'], body.split('\n', 1)[1])


class ManualGroupTests(SimpleTestCase):
    def test_every_key_route_order_and_icon_matches_real_sidebar(self):
        sidebar = (Path(__file__).resolve().parent.parent / 'templates/includes/sidebar_menu.html').read_text(encoding='utf-8')
        definitions = menu_definitions()
        self.assertEqual(definitions, list(OPERATION_MANUALS))
        self.assertEqual(set(MENU_ICONS), {item.menu_key for item in definitions})
        self.assertEqual([label for label, _ in MENU_GROUPS], ['', 'Knowledge', 'Input', 'Management', 'History'])
        positions = []
        for item in definitions:
            needle = "{% url '" + item.url_name + "' %}"
            position = sidebar.index(needle)
            positions.append(position)
            self.assertIn('bi ' + MENU_ICONS[item.menu_key], sidebar[position:sidebar.index('</a>', position)])
        self.assertEqual(positions, sorted(positions))

    def test_empty_missing_groups_and_shuffled_sections_keep_stored_titles(self):
        self.assertEqual(build_menu_groups([]), [])
        sections = [{'manual': SimpleNamespace(menu_key=key, title='編集済み ' + key)}
                    for key in ('users', 'tips', 'home', 'faq', 'view-history')]
        groups = build_menu_groups(sections)
        self.assertEqual([group['title'] for group in groups], ['', 'Knowledge', 'Management', 'History'])
        ordered = [section for group in groups for section in group['sections']]
        self.assertEqual([section['manual'].menu_key for section in ordered],
                         ['home', 'faq', 'tips', 'users', 'view-history'])
        self.assertTrue(all(section in sections for section in ordered))
        self.assertTrue(all(section['manual'].title.startswith('編集済み ') for section in ordered))


class ManualPresentationViewTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='manual-presentation', is_staff=True))

    def test_legacy_alias_semantic_accessible_blocks_escaping_and_no_manual_writes(self):
        body = '独自の前置き\n\n対象・できること\n<b>変更しない</b> & 本文\n\n操作手順\n1. 実行\n未知の見出し\n注意点\n<script>alert(1)</script>\n'
        manual = Manual.objects.create(menu_key='home', title='<独自ホーム>', description=body)
        supplemental = Manual.objects.create(title='補足', description='独自本文\nそのまま\n')
        before = list(Manual.objects.values())
        response = self.client.get(reverse('manual_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Manual.objects.values()), before)
        self.assertEqual(response.context['menu_groups'][0]['sections'][0]['manual'].pk, manual.pk)
        self.assertEqual(response.context['supplemental_sections'][0]['manual'].pk, supplemental.pk)
        parser = RegistrationControlsParser()
        parser.feed(response.content.decode())
        headings = parser.find('h3')
        self.assertEqual([node['text'] for node in headings], ['内容', '操作手順', '注意点'])
        for heading in headings:
            self.assertTrue(parser.find('div', role='region', **{'aria-labelledby': heading['attrs']['id']}))
        self.assertContains(response, escape('<独自ホーム>'))
        self.assertContains(response, escape('<b>変更しない</b> & 本文'))
        self.assertContains(response, escape('<script>alert(1)</script>'))
        self.assertNotContains(response, '<script>alert(1)</script>')
        bodies = [node['text'] for node in parser.nodes if 'manual-text-body' in node['attrs'].get('class', '').split()]
        self.assertEqual(bodies, [block['body'] for block in parse_manual_body(body)] + [supplemental.description])
        self.assertNotContains(response, 'knowledge-form-nav')

    def test_empty_list_has_no_groups_and_still_loads_its_own_navigation(self):
        response = self.client.get(reverse('manual_list'))
        self.assertEqual(response.context['menu_groups'], [])
        self.assertEqual(response.context['manual_sections'], [])
        self.assertContains(response, 'メニュー別の操作手順はまだ登録されていません。')