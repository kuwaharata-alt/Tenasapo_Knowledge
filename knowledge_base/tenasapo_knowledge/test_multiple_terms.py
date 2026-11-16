"""Collective consent and migration coverage, using only the offline test DB."""

from copy import deepcopy
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import TermsAcceptance, TermsOfUse, UserProfile
from .terms import terms_snapshots


@override_settings(MIDDLEWARE=[*settings.MIDDLEWARE,
    'tenasapo_knowledge.middleware.LoginRequiredExceptAssetsMiddleware'])
class CollectiveTermsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = get_user_model().objects.create_user(username='collective-customer')
        UserProfile.objects.create(user=cls.customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.admin = get_user_model().objects.create_user(username='Admin', is_staff=True)
        cls.first = TermsOfUse.objects.create(title='最初の規約', description='第1条（適用）\n1. 最初の本文')
        cls.second = TermsOfUse.objects.create(title='二つ目の規約', description='第2条（追加）\n1. 二つ目の本文')

    def setUp(self):
        self.client.force_login(self.customer)

    def token(self):
        return self.client.get(reverse('terms_accept')).context['terms_token']

    def post(self, token):
        return self.client.post(reverse('terms_accept'), {
            'terms_token': token, 'terms_accepted': 'on',
            'terms_snapshots': '[{"title":"forged"}]',
        })

    def test_one_checkbox_accepts_exact_ordered_set_and_duplicates_do_not_rewrite_it(self):
        page = self.client.get(reverse('terms_accept'))
        self.assertContains(page, self.first.title)
        self.assertContains(page, self.second.title)
        self.assertContains(page, '二つ目の本文')
        self.assertContains(page, 'name="terms_accepted"', count=1)
        self.assertContains(page, 'name="terms_token"', count=1)
        expected = terms_snapshots([self.first, self.second])
        token = page.context['terms_token']
        self.assertRedirects(self.post(token), reverse('home'), fetch_redirect_response=False)
        record = TermsAcceptance.objects.get(user=self.customer)
        self.assertEqual(record.terms_snapshots, expected)
        self.assertEqual(record.terms_title, self.first.title)
        self.assertEqual([doc['updated_at'] for doc in record.accepted_documents],
                         [self.first.updated_at, self.second.updated_at])
        original = deepcopy(TermsAcceptance.objects.values().get())
        self.first.delete()
        TermsOfUse.objects.filter(pk=self.second.pk).update(title='現在のタイトル', description='現在の本文')
        TermsOfUse.objects.create(title='後から追加', description='後から追加した本文')
        for token in (token, '', self.token()):
            self.assertEqual(self.post(token).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.values().get(), original)
        self.client.logout()
        self.client.force_login(self.customer)
        self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
        self.assertEqual(TermsAcceptance.objects.count(), 1)
        # History displays acknowledged titles, never newly edited/current titles.
        self.client.force_login(self.admin)
        history = self.client.get(reverse('user_detail', args=[self.customer.pk]))
        self.assertContains(history, '最初の規約')
        self.assertContains(history, '二つ目の規約')
        self.assertNotContains(history, '現在のタイトル')
        self.assertNotContains(history, '後から追加')
        listing = self.client.get(reverse('user_list'), {'q': self.customer.username})
        self.assertContains(listing, timezone.localtime(record.accepted_at).strftime('%Y/%m/%d %H:%M:%S'))
        self.assertEqual(TermsAcceptance.objects.values().get(), original)

    def test_addition_invalidates_whole_token_and_new_token_accepts_all(self):
        token = self.token()
        third = TermsOfUse.objects.create(title='追加の規約', description='追加本文')
        page = self.post(token)
        self.assertEqual(page.status_code, 409)
        for term in (self.first, self.second, third):
            self.assertContains(page, term.title, status_code=409)
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertNotEqual(page.context['terms_token'], token)
        self.assertEqual(self.post(page.context['terms_token']).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.get().terms_snapshots,
                         terms_snapshots([self.first, self.second, third]))

    def test_deletion_invalidates_whole_token_and_redisplays_remaining_documents(self):
        token = self.token()
        self.first.delete()
        page = self.post(token)
        self.assertContains(page, self.second.title, status_code=409)
        self.assertNotContains(page, '最初の本文', status_code=409)
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertEqual(self.post(page.context['terms_token']).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.get().terms_snapshots, terms_snapshots([self.second]))

    def test_edit_any_document_title_body_or_version_rejects_entire_agreement(self):
        for term in (self.first, self.second):
            for field in ('title', 'description', 'updated_at'):
                with self.subTest(pk=term.pk, field=field):
                    token = self.token()
                    value = timezone.now() if field == 'updated_at' else f'改訂{field}-{term.pk}'
                    TermsOfUse.objects.filter(pk=term.pk).update(**{field: value})
                    term.refresh_from_db()
                    page = self.post(token)
                    self.assertEqual(page.status_code, 409)
                    self.assertEqual(page.context['terms_list'], [self.first, self.second])
                    self.assertContains(page, term.title, status_code=409)
                    self.assertFalse(TermsAcceptance.objects.exists())

    def test_replacing_same_content_with_new_id_invalidates_token(self):
        token = self.token()
        values = {'title': self.second.title, 'description': self.second.description}
        timestamp = self.second.updated_at
        self.second.delete()
        replacement = TermsOfUse.objects.create(**values)
        TermsOfUse.objects.filter(pk=replacement.pk).update(updated_at=timestamp)
        self.assertEqual(self.post(token).status_code, 409)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_legacy_agreement_remains_single_document_and_never_gains_new_consent(self):
        record = TermsAcceptance.objects.create(user=self.customer, terms_title='旧同意タイトル',
            terms_description='実際に同意した旧本文', terms_updated_at=self.first.updated_at)
        original = deepcopy(TermsAcceptance.objects.values().get())
        self.assertEqual(record.terms_snapshots, [])
        self.assertEqual(record.accepted_documents, [{
            'title': '旧同意タイトル', 'description': '実際に同意した旧本文',
            'updated_at': self.first.updated_at,
        }])
        self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
        self.assertEqual(self.post('old-or-invalid-token').status_code, 302)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('user_detail', args=[self.customer.pk]))
        self.assertContains(page, '旧同意タイトル')
        self.assertNotContains(page, self.second.title)
        self.assertEqual(TermsAcceptance.objects.values().get(), original)

    def test_failed_snapshot_write_rolls_back_without_partial_agreement(self):
        token = self.token()
        with patch.object(TermsAcceptance.objects, 'get_or_create', side_effect=RuntimeError('test rollback')):
            with self.assertRaisesMessage(RuntimeError, 'test rollback'):
                self.post(token)
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertEqual(self.post(token).status_code, 302)
        self.assertEqual(len(TermsAcceptance.objects.get().terms_snapshots), 2)


class MultipleTermsMigrationTests(TransactionTestCase):
    def test_existing_rows_history_and_sequence_survive_schema_upgrade(self):
        before = [('tenasapo_knowledge', '0086_terms_acceptance')]
        after = [('tenasapo_knowledge', '0087_multiple_terms')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            old_apps = executor.loader.project_state(before).apps
            terms = old_apps.get_model('tenasapo_knowledge', 'TermsOfUse')
            agreements = old_apps.get_model('tenasapo_knowledge', 'TermsAcceptance')
            users = old_apps.get_model('auth', 'User')
            user = users.objects.create(username='migration-accepted')
            users.objects.create(username='migration-never-accepted')
            term = terms.objects.create(title='現在の本文と異なる規約', description='現在の本文')
            accepted_at = timezone.now()
            agreement = agreements.objects.create(user_id=user.pk, accepted_at=accepted_at,
                terms_title='同意した旧タイトル', terms_description='同意した旧本文',
                terms_updated_at=accepted_at)
            original_term = terms.objects.values().get(pk=term.pk)
            original_agreement = agreements.objects.values().get(pk=agreement.pk)
            if connection.vendor == 'sqlite':
                # The current form omits id, unlike the legacy fixed-PK model.
                # Reproduce the reported POST failure against the old schema.
                with self.assertRaisesMessage(IntegrityError, 'NOT NULL constraint failed'):
                    with transaction.atomic():
                        TermsOfUse.objects.create(title='未移行時の登録', description='検証用本文')
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new_apps = executor.loader.project_state(after).apps
            new_terms = new_apps.get_model('tenasapo_knowledge', 'TermsOfUse')
            new_agreements = new_apps.get_model('tenasapo_knowledge', 'TermsAcceptance')
            self.assertEqual(new_terms.objects.values().get(pk=term.pk), original_term)
            migrated_agreement = new_agreements.objects.values().get(pk=agreement.pk)
            self.assertEqual(migrated_agreement.pop('terms_snapshots'), [])
            self.assertEqual(migrated_agreement, original_agreement)
            if connection.vendor == 'sqlite':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT sql FROM sqlite_master WHERE type=%s AND name=%s',
                                   ['table', new_terms._meta.db_table])
                    sql = cursor.fetchone()[0]
                    cursor.execute('PRAGMA table_info(tenasapo_knowledge_termsofuse)')
                    id_column = next(column for column in cursor.fetchall() if column[1] == 'id')
                self.assertEqual(id_column[2].upper(), 'INTEGER')
                self.assertEqual(id_column[5], 1)
                self.assertIn('PRIMARY KEY AUTOINCREMENT', sql)
                self.assertNotIn('terms_of_use_singleton', sql)
            preserved = new_terms.objects.get(pk=1)
            self.assertEqual(preserved.description, term.description)
            self.assertEqual(preserved.updated_at, term.updated_at)
            additional = new_terms.objects.create(title='追加規約', description='追加本文')
            another = new_terms.objects.create(title='追加規約2', description='追加本文2')
            self.assertGreater(additional.pk, 1)
            self.assertGreater(another.pk, additional.pk)
            self.assertEqual(new_agreements.objects.count(), 1)
            saved = new_agreements.objects.get(pk=agreement.pk)
            self.assertEqual(saved.accepted_at, accepted_at)
            self.assertEqual(saved.terms_updated_at, accepted_at)
            self.assertEqual(saved.terms_title, '同意した旧タイトル')
            self.assertEqual(saved.terms_description, '同意した旧本文')
            self.assertEqual(saved.terms_snapshots, [])
        finally:
            # Always restore schema so later tests never see the legacy model.
            MigrationExecutor(connection).migrate(after)