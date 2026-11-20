"""Initial-password enforcement must not hash passwords on every page view."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .views import INITIAL_PASSWORD_CHECK_SESSION_KEY, should_force_password_change


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class InitialPasswordCacheTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='initial-cache-user', password='initial-cache-user',
        )
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.request.session = {}

    def check(self):
        return should_force_password_change(self.user, request=self.request)

    def test_initial_password_result_is_reused_across_requests(self):
        self.assertTrue(self.check())
        session = json.loads(json.dumps(self.request.session))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.request = RequestFactory().get('/')
        self.request.session = session
        with patch.object(self.user, 'check_password', side_effect=AssertionError('rehash')):
            self.assertTrue(self.check())

    def test_changed_password_result_is_reused(self):
        self.user.set_password('Different-Strong-Password-123')
        self.assertFalse(self.check())
        with patch.object(self.user, 'check_password', side_effect=AssertionError('rehash')):
            self.assertFalse(self.check())

    def test_password_change_invalidates_cached_required_result(self):
        self.assertTrue(self.check())
        self.user.set_password('Different-Strong-Password-123')
        self.assertFalse(self.check())

    def test_password_reset_to_initial_invalidates_cached_exemption(self):
        initial_hash = self.user.password
        self.user.set_password('Different-Strong-Password-123')
        self.assertFalse(self.check())
        get_user_model().objects.filter(pk=self.user.pk).update(
            password=initial_hash,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.check())

    def test_username_change_invalidates_cache(self):
        self.user.username = 'another-login'
        self.assertFalse(self.check())
        self.user.username = 'initial-cache-user'
        self.assertTrue(self.check())

    def test_another_user_cannot_reuse_cache(self):
        self.user.set_password('Different-Strong-Password-123')
        self.assertFalse(self.check())
        self.user = get_user_model().objects.create_user(username='other', password='other')
        self.assertTrue(self.check())

    def test_demo_exemption_does_not_cache_false(self):
        group, _ = Group.objects.get_or_create(name='デモ')
        self.user.groups.add(group)
        self.assertFalse(self.check())
        self.user.groups.remove(group)
        self.assertTrue(self.check())

    def test_demo_exemption_is_checked_before_cached_true(self):
        self.assertTrue(self.check())
        group, _ = Group.objects.get_or_create(name='デモ')
        self.user.groups.add(group)
        self.assertFalse(self.check())

    def test_unusable_password_does_not_reuse_cached_true(self):
        self.assertTrue(self.check())
        self.user.set_unusable_password()
        self.assertFalse(self.check())

    def test_anonymous_user_is_not_checked(self):
        self.assertFalse(should_force_password_change(AnonymousUser(), request=self.request))

    def test_no_session_keeps_legacy_enforcement(self):
        self.assertTrue(should_force_password_change(self.user))

    def test_email_local_part_is_still_initial_password(self):
        self.user.username = 'contract@example.test'
        self.user.set_password('contract')
        self.assertTrue(self.check())
        with patch.object(self.user, 'check_password', side_effect=AssertionError('rehash')):
            self.assertTrue(self.check())

    def test_session_does_not_store_password_or_database_hash(self):
        self.assertTrue(self.check())
        stored = json.dumps(self.request.session)
        self.assertNotIn(self.user.password, stored)
        self.assertNotIn(self.user.username, stored)

    def test_malformed_cache_is_not_an_exemption(self):
        self.assertTrue(self.check())
        valid = self.request.session[INITIAL_PASSWORD_CHECK_SESSION_KEY].copy()
        for invalid in (None, False, [], 'old-format', {},
                        {**valid, 'required': 0},
                        {**valid, 'fingerprint': 'stale', 'required': False}):
            with self.subTest(invalid=invalid):
                self.request.session[INITIAL_PASSWORD_CHECK_SESSION_KEY] = invalid
                self.assertTrue(self.check())

    @override_settings(PASSWORD_HASHERS=[
        'django.contrib.auth.hashers.PBKDF2PasswordHasher',
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ])
    def test_hash_upgrade_uses_post_verification_fingerprint(self):
        old_hash = self.user.password
        self.assertTrue(self.check())
        self.assertNotEqual(self.user.password, old_hash)
        with patch.object(self.user, 'check_password', side_effect=AssertionError('rehash')):
            self.assertTrue(self.check())

    def test_noninitial_pages_do_not_rehash_or_rewrite_session(self):
        self.user.set_password('Different-Strong-Password-123')
        self.user.save(update_fields=['password'])
        self.client.force_login(self.user)
        self.client.get(reverse('home'))
        with patch.object(get_user_model(), 'check_password', side_effect=AssertionError('rehash')):
            for name in ('home', 'article_list'):
                response = self.client.get(reverse(name))
                if name == 'home':
                    # A user without a profile still visits the onboarding LP.
                    self.assertRedirects(response, reverse('login_lp'),
                                         fetch_redirect_response=False)
                else:
                    self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.request.session = session
        session.modified = False
        self.assertFalse(self.check())
        self.assertFalse(session.modified)

    def test_middleware_keeps_blocking_without_rehash(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('home')), reverse('password_change'),
                             fetch_redirect_response=False)
        with patch.object(get_user_model(), 'check_password', side_effect=AssertionError('rehash')):
            for name in ('home', 'article_list', 'password_change'):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200 if name == 'password_change' else 302)

    def test_password_change_clears_enforcement_and_requires_old_password_next_time(self):
        self.client.force_login(self.user)
        self.client.get(reverse('password_change'))
        response = self.client.post(reverse('password_change'), {
            'new_password1': 'Different-Strong-Password-123',
            'new_password2': 'Different-Strong-Password-123',
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('password_change'))
        self.assertIn('old_password', response.context['form'].fields)