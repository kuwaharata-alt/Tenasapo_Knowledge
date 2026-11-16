"""Header presentation metadata, offline requests and real DB constraints."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from .models import TermsAcceptance, TermsOfUse, UserProfile
from .terms import DEFAULT_TERMS_TITLE, terms_fingerprint, terms_snapshots
from .test_review_list_layout import ReviewDOMParser


@override_settings(PASSWORD_MANAGER_USERNAMES=['Admin', 'SystenaAdmin'])
class TermsHeaderTitleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_user(username='Admin', is_staff=True)
        cls.root = users.objects.create_user(username='SystenaAdmin', is_superuser=True)
        cls.customer = users.objects.create_user(username='header-customer')
        UserProfile.objects.create(user=cls.customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.first = TermsOfUse.objects.create(title='既存の主題', description='前文', is_header_title=True)
        cls.second = TermsOfUse.objects.create(title='第1条（適用）', description='1. 本文')

    def save_term(self, term=None, selected=False, **changes):
        data = {'title': term.title if term else '追加の主題',
                'description': term.description if term else '追加の本文', **changes}
        if selected:
            data['is_header_title'] = 'on'
        url = reverse('terms_edit', kwargs={'pk': term.pk}) if term else reverse('terms_edit')
        return self.client.post(url, data)

    def test_create_switch_off_and_edit_for_both_upper_admins(self):
        for user in (self.admin, self.root):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                page = self.client.get(reverse('terms_edit'))
                parser = ReviewDOMParser()
                parser.feed(page.content.decode())
                field, = parser.find(id='id_is_header_title')
                self.assertEqual(field['attrs']['type'], 'checkbox')
                self.assertIn('btn-check', field['attrs']['class'].split())
                self.assertNotIn('required', field['attrs'])
                label, = [node for node in parser.find(tag='label')
                          if node['attrs'].get('for') == 'id_is_header_title' and 'btn' in node['attrs'].get('class', '').split()]
                self.assertEqual(label['text'], 'タイトルに使用')
                self.assertEqual(self.save_term(selected=True).status_code, 302)
                new = TermsOfUse.objects.latest('pk')
                self.assertEqual(list(TermsOfUse.objects.filter(is_header_title=True)), [new])
                self.assertTrue(self.client.get(reverse('terms_edit', args=[new.pk])).context['form']['is_header_title'].value())
                self.assertEqual(self.save_term(self.second, selected=True).status_code, 302)
                self.assertEqual(list(TermsOfUse.objects.filter(is_header_title=True)), [self.second])
                self.assertEqual(self.save_term(new, selected=False, title='非選択の変更').status_code, 302)
                self.assertEqual(TermsOfUse.objects.get(is_header_title=True).pk, self.second.pk)
                self.assertEqual(self.save_term(self.second).status_code, 302)
                self.assertFalse(TermsOfUse.objects.filter(is_header_title=True).exists())
                self.assertEqual(self.client.get(reverse('terms_detail')).context['default_terms_title'], DEFAULT_TERMS_TITLE)

    def test_validation_failure_does_not_clear_old_choice_or_create_partial_row(self):
        self.client.force_login(self.admin)
        before = list(TermsOfUse.objects.values())
        for term in (None, self.second):
            response = self.save_term(term, selected=True, description='')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
            self.assertTrue(response.context['form']['is_header_title'].value())
            self.assertEqual(list(TermsOfUse.objects.values()), before)

    def test_write_failure_rolls_back_old_choice_for_create_and_update(self):
        self.client.force_login(self.admin)
        before = list(TermsOfUse.objects.values())
        for term in (None, self.second):
            with patch.object(TermsOfUse, 'save', side_effect=RuntimeError('save failed')):
                with self.assertRaisesMessage(RuntimeError, 'save failed'):
                    self.save_term(term, selected=True)
            self.assertEqual(list(TermsOfUse.objects.values()), before)

    def test_constraint_rejects_illegal_create_bulk_and_queryset_flags(self):
        for write in (
            lambda: TermsOfUse.objects.create(title='不正', description='本文', is_header_title=True),
            lambda: TermsOfUse.objects.filter(pk=self.second.pk).update(is_header_title=True),
            lambda: TermsOfUse.objects.bulk_create([TermsOfUse(title='不正', description='本文', is_header_title=True)]),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                write()
            self.assertEqual(list(TermsOfUse.objects.filter(is_header_title=True)), [self.first])

    def test_selected_title_escaped_once_in_header_not_in_scroll_for_all_readers(self):
        self.first.title = '<img src=x onerror=alert(1)> & "主題"'
        self.first.save()
        # Exact old default text no longer implicitly hides an unselected title.
        legacy = TermsOfUse.objects.create(title=DEFAULT_TERMS_TITLE, description='残す前文')
        for user in (None, self.admin, self.root, self.customer):
            if user:
                self.client.force_login(user)
            else:
                self.client.logout()
            names = ('terms_detail', 'terms_accept') if user == self.customer else ('terms_detail',)
            for name in names:
                with self.subTest(user=str(user), page=name):
                    page = self.client.get(reverse(name))
                    self.assertEqual(page.status_code, 200)
                    parser = ReviewDOMParser()
                    html = page.content.decode()
                    parser.feed(html)
                    header, = parser.find(id='terms-page-title')
                    self.assertEqual(header['text'], self.first.title)
                    browser_title, = parser.find(tag='title')
                    self.assertIn(self.first.title, browser_title['text'])
                    self.assertEqual([node['text'] for node in parser.find(css_class='terms-document-title')],
                                     [self.second.title, legacy.title])
                    self.assertEqual(len(parser.find(css_class='terms-article-heading')), 1)
                    self.assertIn(str(escape(self.first.title)), html)
                    self.assertNotIn('&amp;lt;img', html)
                    self.assertNotIn('<img src=x', html)
                    self.assertEqual(len([node for node in parser.find(tag='h1') if node['text'] == self.first.title]), 1)

    def test_metadata_switch_preserves_versions_token_and_existing_snapshots(self):
        original_terms = list(TermsOfUse.objects.values())
        before = terms_snapshots(list(TermsOfUse.objects.all()))
        fingerprint = terms_fingerprint(list(TermsOfUse.objects.all()))
        self.client.force_login(self.customer)
        token = self.client.get(reverse('terms_accept')).context['terms_token']
        self.client.force_login(self.admin)
        self.assertEqual(self.save_term(self.second, selected=True).status_code, 302)
        self.assertEqual(terms_snapshots(list(TermsOfUse.objects.all())), before)
        self.assertEqual(terms_fingerprint(list(TermsOfUse.objects.all())), fingerprint)
        self.client.force_login(self.customer)
        response = self.client.post(reverse('terms_accept'), {'terms_token': token, 'terms_accepted': 'on'})
        self.assertEqual(response.status_code, 302)
        acceptance = deepcopy(TermsAcceptance.objects.values().get())
        self.assertEqual(acceptance['terms_snapshots'], before)
        self.client.force_login(self.admin)
        self.assertEqual(self.save_term(self.first, selected=True).status_code, 302)
        self.assertEqual(self.save_term(self.first, selected=False).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.values().get(), acceptance)
        for actual, original in zip(TermsOfUse.objects.values(), original_terms):
            self.assertEqual({key: value for key, value in actual.items() if key != 'is_header_title'},
                             {key: value for key, value in original.items() if key != 'is_header_title'})

    def test_legal_edit_still_updates_version_and_invalidates_token(self):
        self.client.force_login(self.customer)
        token = self.client.get(reverse('terms_accept')).context['terms_token']
        self.client.force_login(self.admin)
        timestamp = self.first.updated_at
        self.assertEqual(self.save_term(self.first, selected=True, title='変更した主題').status_code, 302)
        self.first.refresh_from_db()
        self.assertGreater(self.first.updated_at, timestamp)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('terms_accept')).context['default_terms_title'], '変更した主題')
        self.assertEqual(self.client.post(reverse('terms_accept'), {
            'terms_token': token, 'terms_accepted': 'on'}).status_code, 409)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_writer_reservation_precedes_fresh_read_and_clear(self):
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.save_term(self.second, selected=True).status_code, 302)
        sql = [query['sql'] for query in queries]
        reservation = next(index for index, query in enumerate(sql)
                           if query.startswith('UPDATE "tenasapo_knowledge_termsofuse" SET "is_header_title" = ')
                           and 'WHERE' not in query)
        fresh_read = next(index for index, query in enumerate(sql)
                          if index > reservation and query.startswith('SELECT') and 'tenasapo_knowledge_termsofuse' in query)
        clear = next(index for index, query in enumerate(sql)
                     if index > reservation and query.startswith('UPDATE') and 'WHERE' in query)
        self.assertLess(reservation, fresh_read)
        self.assertLess(fresh_read, clear)


class TermsHeaderMigrationTests(TransactionTestCase):
    def test_migration_only_selects_first_exact_legacy_main_title_and_preserves_everything(self):
        before = [('tenasapo_knowledge', '0087_multiple_terms')]
        after = [('tenasapo_knowledge', '0088_terms_header_title')]
        for titles in (['第1条（適用）', '任意のタイトル'],
                       ['第1条（適用）', ' ' + DEFAULT_TERMS_TITLE + ' ', DEFAULT_TERMS_TITLE]):
            with self.subTest(titles=titles):
                executor = MigrationExecutor(connection)
                executor.migrate(before)
                try:
                    apps = executor.loader.project_state(before).apps
                    terms = apps.get_model('tenasapo_knowledge', 'TermsOfUse')
                    agreements = apps.get_model('tenasapo_knowledge', 'TermsAcceptance')
                    users = apps.get_model('auth', 'User')
                    terms.objects.all().delete()
                    for title in titles:
                        terms.objects.create(title=title, description='既存の本文\n1. 保持')
                    originals = list(terms.objects.values())
                    user = users.objects.create(username=f'migrate-header-{len(titles)}')
                    agreements.objects.create(user_id=user.pk, terms_title='旧同意タイトル',
                        terms_description='旧同意本文', terms_updated_at=originals[0]['updated_at'],
                        terms_snapshots=[{'title': '過去の主題', 'description': '過去の本文'}])
                    histories = list(agreements.objects.values())
                    executor = MigrationExecutor(connection)
                    executor.migrate(after)
                    apps = executor.loader.project_state(after).apps
                    current = apps.get_model('tenasapo_knowledge', 'TermsOfUse')
                    migrated = list(current.objects.values())
                    flags = [row.pop('is_header_title') for row in migrated]
                    self.assertEqual(flags, [False, True, False] if len(titles) == 3 else [False, False])
                    self.assertEqual(migrated, originals)
                    self.assertEqual(list(apps.get_model('tenasapo_knowledge', 'TermsAcceptance').objects.values()), histories)
                finally:
                    executor = MigrationExecutor(connection)
                    executor.migrate(executor.loader.graph.leaf_nodes())