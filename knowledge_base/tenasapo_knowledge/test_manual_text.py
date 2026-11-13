"""Editable plaintext manuals retain legacy PDF files and existing access rules."""

from pathlib import Path
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils.html import escape

from .forms import ManualForm
from .manual_defaults import OPERATION_MANUALS
from .models import Manual
from .test_registration_form_layout import RegistrationControlsParser


class ManualTextTests(TestCase):
    body = '1. メニューを開く\n2. <script>alert("x")</script>\n<b>文字のまま</b> & 確認'

    def setUp(self):
        directory = TemporaryDirectory(prefix='nexus-manual-text-')
        self.addCleanup(directory.cleanup)
        self.storage = FileSystemStorage(location=directory.name, base_url='/test-manuals/')
        self.enterContext(patch.object(Manual._meta.get_field('pdf_file'), 'storage', self.storage))
        users = get_user_model()
        self.staff = users.objects.create_user(username='manual-text-staff', is_staff=True)
        self.superuser = users.objects.create_user(
            username='manual-text-superuser', is_superuser=True, is_staff=False,
        )
        self.reader = users.objects.create_user(username='manual-text-reader')
        self.client.force_login(self.staff)
        self.legacy_name = self.storage.save('manuals/legacy.pdf', ContentFile(b'%PDF-legacy'))
        self.manual = Manual.objects.create(
            title='ホームの使い方', description=self.body, pdf_file=self.legacy_name,
            order=3, is_published=True, created_by=self.staff,
        )

    def payload(self, **overrides):
        return {'title': 'FAQの使い方', 'description': self.body, 'order': '0',
                'is_published': 'on', **overrides}

    def parse(self, response):
        parser = RegistrationControlsParser()
        parser.feed(response.content.decode(response.charset))
        return parser

    def assert_no_pdf_ui(self, response):
        html = response.content.decode(response.charset)
        self.assertNotIn(self.legacy_name, html)
        self.assertNotIn('pdf.js', html.lower())
        self.assertNotIn('pdfjs', html.lower())
        self.assertNotIn('pdf-viewer', html)
        self.assertNotIn('PDFダウンロード', html)
        parser = self.parse(response)
        self.assertFalse(parser.find('canvas'))
        for tag in ('iframe', 'embed', 'object'):
            self.assertFalse(parser.find(tag))
        self.assertFalse(parser.find('input', type='file'))
        self.assertNotIn('pdf_file', parser.controls)
        return parser

    def assert_legacy_preserved(self):
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.pdf_file.name, self.legacy_name)
        self.assertEqual(Path(self.storage.path(self.legacy_name)).read_bytes(), b'%PDF-legacy')

    def test_form_requires_text_body_not_pdf_and_model_allows_legacy_empty_body(self):
        self.assertTrue(Manual._meta.get_field('pdf_file').blank)
        self.assertTrue(Manual._meta.get_field('description').blank)
        form = ManualForm(self.payload())
        self.assertNotIn('pdf_file', form.fields)
        self.assertEqual(form.fields['description'].label, '使い方・操作手順')
        self.assertTrue(form.fields['description'].required)
        self.assertEqual(form.fields['description'].widget.attrs['rows'], 18)
        self.assertTrue(form.is_valid(), form.errors)
        for body in ('', ' \t\n '):
            with self.subTest(body=body):
                form = ManualForm(self.payload(description=body), instance=self.manual)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors.as_data()['description'][0].code, 'required')

    def test_create_without_pdf_saves_text_order_publication_and_creator(self):
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(reverse('manual_create'), self.payload())
                self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
                item = Manual.objects.latest('pk')
                self.assertEqual(item.description, self.body)
                self.assertFalse(item.pdf_file)
                self.assertEqual(item.order, 0)
                self.assertTrue(item.is_published)
                self.assertEqual(item.created_by_id, user.pk)
        self.assert_legacy_preserved()

    def test_edit_body_preserves_physical_pdf_even_with_obsolete_post_controls(self):
        with patch.object(self.storage, 'delete', wraps=self.storage.delete) as delete:
            response = self.client.post(reverse('manual_edit', args=[self.manual.pk]), self.payload(
                description='変更した手順\n次の操作', order='8', is_published='',
                **{'pdf_file-clear': 'on', 'clear_pdf': 'on',
                   'pdf_file': SimpleUploadedFile('replacement.pdf', b'%PDF-replacement')},
            ))
            delete.assert_not_called()
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        self.assert_legacy_preserved()
        self.assertEqual(self.manual.description, '変更した手順\n次の操作')
        self.assertEqual(self.manual.order, 8)
        self.assertFalse(self.manual.is_published)
        self.assertEqual(self.manual.created_by_id, self.staff.pk)

    def test_missing_body_rerenders_without_create_or_update(self):
        before = Manual.objects.count()
        for route, args in (('manual_create', []), ('manual_edit', [self.manual.pk])):
            with self.subTest(route=route):
                response = self.client.post(reverse(route, args=args), self.payload(description=''))
                self.assertEqual(response.status_code, 200)
                self.assertIn('description', response.context['form'].errors)
                self.assert_no_pdf_ui(response)
                self.assertEqual(Manual.objects.count(), before)
                self.assert_legacy_preserved()
                self.assertEqual(self.manual.description, self.body)

    def test_create_and_edit_navigation_matches_text_fields(self):
        for route, args in (('manual_create', []), ('manual_edit', [self.manual.pk])):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 200)
                parser = self.assert_no_pdf_ui(response)
                expected = ['form-section-' + key for key in ('title', 'description', 'order', 'is_published')]
                self.assertEqual(parser.section_ids, expected)
                self.assertEqual(parser.section_links, expected)
                self.assertIn('required', parser.controls['description'])
                self.assertEqual(parser.controls['description']['rows'], '18')
                if args:
                    self.assertContains(response, escape(self.body))
                    self.assertNotContains(response, '<script>alert("x")</script>')

    def test_detail_plaintext_is_escaped_preserves_newlines_and_has_section_links(self):
        response = self.client.get(reverse('manual_detail', args=[self.manual.pk]))
        self.assertEqual(response.status_code, 200)
        parser = self.assert_no_pdf_ui(response)
        body, = [node for node in parser.nodes
                 if 'manual-text-body' in node['attrs'].get('class', '').split()]
        self.assertEqual(body['text'], self.body)
        self.assertIn('white-space: pre-wrap', body['attrs']['style'])
        self.assertContains(response, escape(self.body))
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertFalse(parser.find('b'))
        for key in ('overview', 'body', 'settings'):
            self.assertEqual(len(parser.find(id='manual-' + key)), 1)
            self.assertEqual(len(parser.find('a', href='#manual-' + key)), 1)
        self.assertTrue(parser.find('a', href=reverse('manual_edit', args=[self.manual.pk])))

    def test_list_keeps_legacy_text_as_full_escaped_supplemental_sections(self):
        unpublished = Manual.objects.create(title='<b>非公開手順</b>', description=self.body,
                                            order=0, is_published=False)
        response = self.client.get(reverse('manual_list'))
        self.assertEqual(response.status_code, 200)
        parser = self.assert_no_pdf_ui(response)
        self.assertEqual([item.pk for item in response.context['manuals']],
                         [unpublished.pk, self.manual.pk])
        self.assertContains(response, '&lt;b&gt;非公開手順&lt;/b&gt;')
        self.assertNotContains(response, '<b>非公開手順</b>')
        self.assertContains(response, '非公開')
        self.assertContains(response, '表示順 0')
        self.assertContains(response, escape(self.body))
        for item in (unpublished, self.manual):
            anchor = f'manual-legacy-{item.pk}'
            self.assertTrue(parser.find('a', href='#' + anchor))
            self.assertTrue(parser.find(id=anchor))
            edit = reverse('manual_edit', args=[item.pk]) + '?' + urlencode({
                'next': reverse('manual_list') + '#' + anchor,
            }, safe='/')
            self.assertTrue(parser.find('a', href=edit))
            self.assertFalse(parser.find('a', href=reverse('manual_detail', args=[item.pk])))

    def test_legacy_empty_body_renders_without_touching_pdf_or_publication(self):
        self.manual.description = ''
        self.manual.is_published = False
        self.manual.save(update_fields=['description', 'is_published'])
        for route, args in (('manual_list', []), ('manual_detail', [self.manual.pk])):
            response = self.client.get(reverse(route, args=args))
            self.assertEqual(response.status_code, 200)
            if route == 'manual_list':
                self.assertNotContains(response, self.manual.title)
                self.assertEqual(response.context['manual_sections'], [])
            else:
                self.assertContains(response, '操作手順はまだ登録されていません。')
                self.assertContains(response, '非公開')
            self.assert_no_pdf_ui(response)
        self.assert_legacy_preserved()
        self.assertFalse(self.manual.is_published)

    def test_nonstaff_and_anonymous_cannot_read_or_write_manuals(self):
        specs = [('manual_list', [], 'get'), ('manual_detail', [self.manual.pk], 'get'),
                 ('manual_create', [], 'get'), ('manual_create', [], 'post'),
                 ('manual_edit', [self.manual.pk], 'get'), ('manual_edit', [self.manual.pk], 'post'),
                 ('manual_delete', [self.manual.pk], 'post')]
        for user in (self.reader, None):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for route, args, method in specs:
                with self.subTest(user=user, route=route, method=method):
                    response = getattr(self.client, method)(reverse(route, args=args),
                                                            self.payload() if method == 'post' else {})
                    if user:
                        self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
                    else:
                        self.assertRedirects(
                            response, reverse('login') + '?' + urlencode({'next': reverse(route, args=args)}, safe='/'),
                            fetch_redirect_response=False,
                        )
                    self.assertEqual(Manual.objects.count(), 1)
                    self.assert_legacy_preserved()
                    self.assertEqual(self.manual.description, self.body)

    def seed(self):
        output = StringIO()
        call_command('seed_operation_manuals', stdout=output)
        return output.getvalue()

    def test_seed_validates_all_fifteen_sidebar_routes_and_creates_only_missing(self):
        self.assertEqual(len(OPERATION_MANUALS), 15)
        self.assertEqual(len({item.menu_key for item in OPERATION_MANUALS}), 15)
        sidebar = (Path(__file__).resolve().parent.parent / 'templates/includes/sidebar_menu.html').read_text(encoding='utf-8')
        positions = []
        for definition in OPERATION_MANUALS:
            self.assertTrue(reverse(definition.url_name))
            positions.append(sidebar.index("{% url '" + definition.url_name + "' %}"))
            for heading in ('内容', '操作手順', '1. ', '2. ', '注意点'):
                self.assertIn(heading, definition.description)
        self.assertEqual(positions, sorted(positions))
        self.assertIn('追加 15 件', self.seed())
        self.assertEqual(Manual.objects.count(), 16)
        for order, definition in enumerate(OPERATION_MANUALS):
            item = Manual.objects.get(menu_key=definition.menu_key)
            self.assertEqual((item.title, item.description, item.order),
                             (definition.title, definition.description, order))
            self.assertFalse(item.pdf_file)
        self.assert_legacy_preserved()

    def test_seed_never_overwrites_custom_title_body_settings_file_or_timestamps(self):
        self.manual.menu_key = 'home'
        self.manual.title = '独自のホーム手順'
        self.manual.is_published = False
        self.manual.order = 99
        self.manual.save()
        before = Manual.objects.values().get(pk=self.manual.pk)
        self.assertIn('追加 14 件', self.seed())
        self.assertIn('追加 0 件', self.seed())
        self.assertEqual(Manual.objects.values().get(pk=self.manual.pk), before)
        self.assertEqual(Manual.objects.count(), 15)
        missing = Manual.objects.get(menu_key='tips')
        missing.delete()
        self.assertIn('追加 1 件', self.seed())
        self.assertEqual(Manual.objects.values().get(pk=self.manual.pk), before)
        self.assert_legacy_preserved()

    def test_list_shows_all_seeded_full_bodies_menu_order_links_and_no_delete(self):
        self.seed()
        home = Manual.objects.get(menu_key='home')
        home.description = self.body + '\n' + '長い本文' * 100 + '\n末尾まで表示'
        home.title = '<b>独自ホーム</b>'
        home.order = 999
        home.is_published = False
        home.save()
        pdf_only = Manual.objects.create(title='旧ファイルのみ', pdf_file=self.legacy_name)
        response = self.client.get(reverse('manual_list'))
        parser = self.assert_no_pdf_ui(response)
        self.assertEqual([section['manual'].menu_key for section in response.context['menu_sections']],
                         [definition.menu_key for definition in OPERATION_MANUALS])
        self.assertEqual(len(response.context['menu_sections']), 15)
        for section in response.context['menu_sections']:
            item = section['manual']
            # Headings now have semantic markup; every plaintext body remains
            # escaped and the presentation blocks reconstruct the stored text.
            self.assertEqual(''.join(block['source_heading'] + block['body']
                                     for block in section['blocks']), item.description)
            for block in section['blocks']:
                self.assertContains(response, escape(block['body']))
            for heading in ('内容', '操作手順', '注意点') if item != home else ():
                self.assertTrue(any(node['text'] == heading for node in parser.find('h3')))
            self.assertTrue(parser.find(id=section['anchor']))
            self.assertTrue(parser.find('a', href='#' + section['anchor']))
            definition = next(entry for entry in OPERATION_MANUALS if entry.menu_key == item.menu_key)
            self.assertEqual(section['menu_url'], reverse(definition.url_name))
            if item.menu_key == 'view-history':
                self.assertFalse(parser.find('a', href=section['menu_url']))
            else:
                self.assertTrue(parser.find('a', href=section['menu_url']))
            edit = reverse('manual_edit', args=[item.pk]) + '?' + urlencode({'next': section['return_to']}, safe='/')
            self.assertTrue(parser.find('a', href=edit))
            self.assertFalse(parser.find('form', action=reverse('manual_delete', args=[item.pk])))
            self.assertFalse(parser.find('a', href=reverse('manual_detail', args=[item.pk])))
        bodies = [node for node in parser.nodes if 'manual-text-body' in node['attrs'].get('class', '').split()]
        self.assertEqual(bodies[0]['text'], home.description)
        self.assertIn('white-space: pre-wrap', bodies[0]['attrs']['style'])
        self.assertContains(response, escape(home.title))
        self.assertNotContains(response, '<b>独自ホーム</b>')
        self.assertNotContains(response, pdf_only.title)
        self.assertTrue(Manual.objects.filter(pk=pdf_only.pk).exists())
        self.assert_legacy_preserved()

    def test_edit_keyed_body_returns_to_same_anchor_and_ignores_menu_key_post(self):
        self.manual.menu_key = 'home'
        self.manual.save(update_fields=['menu_key'])
        target = reverse('manual_list') + '#manual-home'
        edit = reverse('manual_edit', args=[self.manual.pk])
        response = self.client.get(edit, {'next': target})
        parser = self.parse(response)
        self.assertNotIn('menu_key', parser.controls)
        self.assertEqual(parser.controls['next']['value'], target)
        self.assertTrue(parser.find('a', href=target))
        response = self.client.post(edit, self.payload(description=self.body + '\n編集済み', next=target, menu_key='tips'))
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.assert_legacy_preserved()
        self.assertEqual(self.manual.menu_key, 'home')
        self.assertContains(self.client.get(reverse('manual_list')), escape(self.body + '\n編集済み'))
        invalid = self.client.post(edit, self.payload(description='', next=target))
        self.assertEqual(self.parse(invalid).controls['next']['value'], target)

    def test_unsafe_next_is_rejected_for_get_post_and_https_downgrade(self):
        edit = reverse('manual_edit', args=[self.manual.pk])
        for target in ('https://evil.example/path', '//evil.example/path', 'javascript:alert(1)', '/\\evil.example/path'):
            with self.subTest(target=target):
                response = self.client.get(edit, {'next': target})
                self.assertEqual(response.context['return_to'], reverse('manual_list'))
                response = self.client.post(edit, self.payload(next=target))
                self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        response = self.client.post(edit, self.payload(next='http://testserver/manuals/'), secure=True)
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)

    def test_keyed_delete_post_and_admin_bulk_protected_legacy_file_retained(self):
        self.manual.menu_key = 'home'
        self.manual.save(update_fields=['menu_key'])
        route = reverse('manual_delete', args=[self.manual.pk])
        self.assertEqual(self.client.get(route).status_code, 405)
        response = self.client.post(route)
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('manual_list')), '固定メニューの操作手順は削除できません。')
        self.assert_legacy_preserved()
        legacy = Manual.objects.create(title='補足', description='補足手順', pdf_file=self.legacy_name)
        request = RequestFactory().post('/')
        request.user = self.superuser
        model_admin = admin.site._registry[Manual]
        self.assertFalse(model_admin.has_delete_permission(request, self.manual))
        self.assertTrue(model_admin.has_delete_permission(request, legacy))
        with patch.object(self.storage, 'delete', wraps=self.storage.delete) as delete:
            model_admin.delete_queryset(request, Manual.objects.filter(pk__in=[self.manual.pk, legacy.pk]))
            delete.assert_not_called()
        self.assertTrue(Manual.objects.filter(pk=self.manual.pk).exists())
        self.assertFalse(Manual.objects.filter(pk=legacy.pk).exists())
        legacy = Manual.objects.create(title='補足2', description='本文', pdf_file=self.legacy_name)
        with patch.object(self.storage, 'delete', wraps=self.storage.delete) as delete:
            response = self.client.post(reverse('manual_delete', args=[legacy.pk]))
            delete.assert_not_called()
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        self.assertFalse(Manual.objects.filter(pk=legacy.pk).exists())
        self.assert_legacy_preserved()

    def test_menu_key_unique_nullable_noneditable_and_migration_dependencies(self):
        field = Manual._meta.get_field('menu_key')
        self.assertTrue(field.null and field.blank and field.unique)
        self.assertFalse(field.editable)
        self.assertEqual(field.max_length, 64)
        Manual.objects.create(title='補足', description='本文')
        self.manual.menu_key = 'home'
        self.manual.save(update_fields=['menu_key'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Manual.objects.create(title='重複', menu_key='home')
        loader = MigrationLoader(None)
        migration = loader.disk_migrations[('tenasapo_knowledge', '0075_manual_menu_key')]
        self.assertEqual(migration.dependencies, [('tenasapo_knowledge', '0074_alter_manual_pdf_file')])
        state = loader.project_state([('tenasapo_knowledge', '0075_manual_menu_key')])
        historical = state.apps.get_model('tenasapo_knowledge', 'Manual')
        self.assertTrue(historical._meta.get_field('pdf_file').blank)
        self.assertTrue(historical._meta.get_field('menu_key').null)

    def test_superuser_can_read_full_manual_and_customer_demo_modes_cannot(self):
        self.seed()
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse('manual_list')).status_code, 200)
        for mode in ('demo', 'cs'):
            self.client.force_login(self.staff)
            session = self.client.session
            session['account_view_mode'] = mode
            session.save()
            for route, args, method in (('manual_list', [], 'get'), ('manual_edit', [self.manual.pk], 'post'),
                                        ('manual_delete', [self.manual.pk], 'post')):
                response = getattr(self.client, method)(reverse(route, args=args), self.payload())
                self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
            self.assert_legacy_preserved()