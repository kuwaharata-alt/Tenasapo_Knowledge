from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
import logging

from .models import Customer, UserProfile

logger = logging.getLogger(__name__)


class ExistingUserGoogleAdapter(DefaultSocialAccountAdapter):
    def get_app(self, request, provider, client_id=None):
        """複数のSocialAppがある場合に最初のものを返す"""
        try:
            return super().get_app(request, provider, client_id)
        except Exception:
            # get_app() で MultipleObjectsReturned が発生した場合
            apps = SocialApp.objects.filter(provider=provider)
            if apps.exists():
                return apps.first()
            raise

    def pre_social_login(self, request, sociallogin):
        provider = sociallogin.account.provider
        if provider != 'google':
            return

        account_email = ''
        if sociallogin.user and sociallogin.user.email:
            account_email = sociallogin.user.email.strip()
        if not account_email:
            account_email = str(sociallogin.account.extra_data.get('email') or '').strip()

        request.session['auth_provider'] = provider
        request.session['auth_account_email'] = account_email
        request.session['auth_account_uid'] = str(sociallogin.account.uid or '')

        if sociallogin.is_existing:
            return

        email = ''
        if sociallogin.user and sociallogin.user.email:
            email = sociallogin.user.email.strip()

        if not email:
            email = str(sociallogin.account.extra_data.get('email') or '').strip()

        existing_user = self._resolve_existing_user(email)

        if existing_user:
            sociallogin.connect(request, existing_user)
            # 既存ユーザーへのGoogle初回連携：初回登録フォームを表示するためフラグをリセット
            try:
                profile = existing_user.knowledge_profile
                if profile.google_first_login_done:
                    profile.google_first_login_done = False
                    profile.save(update_fields=['google_first_login_done'])
            except Exception:
                pass
            return

        auto_create_enabled = bool(getattr(settings, 'GOOGLE_AUTO_CREATE_USER', True))
        if not auto_create_enabled:
            messages.error(
                request,
                _('このGoogleアカウントではログインできません。事前に管理者へユーザー登録を依頼してください。'),
            )
            raise ImmediateHttpResponse(redirect(getattr(settings, 'LOGIN_URL', 'login')))

        created_user = self._create_user_from_google(email=email, sociallogin=sociallogin)
        sociallogin.connect(request, created_user)

    def _resolve_existing_user(self, email):
        User = get_user_model()
        users = User.objects.filter(email__iexact=email, is_active=True).order_by('id')
        if not users.exists():
            return None

        local_part = (email.split('@')[0] if '@' in email else '').strip().lower()
        if local_part:
            exact_username_user = users.filter(username__iexact=local_part).first()
            if exact_username_user:
                return exact_username_user

        non_admin_username_user = users.exclude(username__iexact='admin').first()
        if non_admin_username_user:
            return non_admin_username_user

        return users.first()

    def _create_user_from_google(self, email, sociallogin):
        User = get_user_model()
        username = self._build_unique_username(email)

        # Workspace からユーザー情報を取得
        workspace_info = self._get_workspace_user_info(email)

        display_name = (
            workspace_info.get('display_name')
            or str(
                sociallogin.account.extra_data.get('name')
                or sociallogin.account.extra_data.get('given_name')
                or username
            ).strip()
        )

        email_domain = (email.split('@')[-1] if '@' in email else '').strip().lower()
        is_systena_domain = email_domain == 'systena.co.jp'

        company_name = 'システナ' if is_systena_domain else (
            str(getattr(settings, 'GOOGLE_DEFAULT_COMPANY_NAME', 'Googleログインユーザー')).strip()
            or 'Googleログインユーザー'
        )

        user_type = UserProfile.USER_TYPE_SYSTENA if is_systena_domain else UserProfile.USER_TYPE_CUSTOMER

        is_admin = False

        customer_group_name = getattr(
            settings,
            'USER_ROLE_CUSTOMER_NAME',
            getattr(settings, 'USER_GROUP_CUSTOMER_NAME', 'カスタマー'),
        )

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                password=None,
                email=email,
                is_staff=is_admin,
                is_superuser=is_admin,
            )
            user.set_unusable_password()
            user.save(update_fields=['password'])

            customer_group, _ = user.groups.model.objects.get_or_create(name=customer_group_name)
            user.groups.add(customer_group)

            UserProfile.objects.create(
                user=user,
                uid=workspace_info.get('uid'),
                display_name=display_name,
                company_name=company_name,
                department=workspace_info.get('department', ''),
                group=workspace_info.get('group', ''),
                position=workspace_info.get('position', ''),
                user_type=user_type,
                email_addresses=email,
                note='Google初回ログインで自動作成',
            )

            customer, _ = Customer.objects.get_or_create(name=company_name)
            customer.users.add(user)

        return user

    def _build_unique_username(self, email):
        User = get_user_model()
        base = (email.split('@')[0] if '@' in email else email).strip().lower()
        allowed = 'abcdefghijklmnopqrstuvwxyz0123456789._-'
        normalized = ''.join(ch for ch in base if ch in allowed)
        if not normalized:
            normalized = 'googleuser'
        normalized = normalized[:30]

        candidate = normalized
        suffix = 2
        while User.objects.filter(username=candidate).exists():
            candidate = f'{normalized[:26]}-{suffix}'
            suffix += 1
        return candidate

    def _get_workspace_user_info(self, email):
        """
        Google Directory API から Workspace ユーザー情報を取得
        社員番号、部署、グループ、役職を取得して辞書で返す
        """
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            import json
        except ImportError:
            logger.warning('Google API libraries not available')
            return {}

        try:
            service_account_json = getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON_PATH', None)
            workspace_domain = getattr(settings, 'GOOGLE_WORKSPACE_DOMAIN', None)

            if not service_account_json or not workspace_domain:
                logger.debug('Workspace integration not configured')
                return {}

            # サービスアカウント credentials を作成
            credentials = service_account.Credentials.from_service_account_file(
                service_account_json,
                scopes=['https://www.googleapis.com/auth/admin.directory.user.readonly']
            )

            # サブジェクト（実行ユーザー）を指定
            admin_email = getattr(settings, 'GOOGLE_WORKSPACE_ADMIN_EMAIL', None)
            if admin_email:
                credentials = credentials.with_subject(admin_email)

            # Directory API クライアントを作成
            service = build('admin', 'directory_v1', credentials=credentials)

            # ユーザー情報を取得（customSchemas も含める）
            user = service.users().get(
                userKey=email,
                projection='custom'  # カスタム属性も取得
            ).execute()

            result = {}

            # 基本情報
            if 'name' in user:
                result['display_name'] = user['name'].get('fullName', '')

            organizations = user.get('organizations') or []
            if organizations and isinstance(organizations, list):
                primary_org = organizations[0] if isinstance(organizations[0], dict) else {}
                result['department'] = (
                    result.get('department')
                    or primary_org.get('department')
                    or primary_org.get('name')
                    or ''
                )

            # カスタム属性を検索
            if 'customSchemas' in user:
                custom_schemas = user['customSchemas']
                # スキーマ名は環境に応じて異なる可能性があるので、複数試す
                for schema_name, schema_data in custom_schemas.items():
                    if isinstance(schema_data, dict):
                        result['uid'] = schema_data.get('uid') or schema_data.get('employee_id') or schema_data.get('employee_number')
                        result['department'] = schema_data.get('department') or schema_data.get('部署')
                        result['group'] = schema_data.get('group') or schema_data.get('グループ')
                        result['position'] = schema_data.get('position') or schema_data.get('役職')

            logger.info(f'Workspace info retrieved for {email}: {result}')
            return result

        except Exception as e:
            logger.error(f'Failed to get Workspace user info for {email}: {str(e)}')
            return {}
