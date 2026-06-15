from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.crypto import get_random_string

from tenasapo_knowledge.utils import resolve_user_display_name


class Command(BaseCommand):
    help = '指定ユーザーのパスワードをランダム再発行し、通知メールを送信します。'

    @staticmethod
    def parse_email_addresses(value):
        normalized = value or ''
        for separator in (',', ';', '、'):
            normalized = normalized.replace(separator, '\n')
        return [email.strip() for email in normalized.splitlines() if email.strip()]

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default='cs-demo',
            help='対象ユーザーのログインID。既定値は cs-demo です。',
        )
        parser.add_argument(
            '--recipient-email',
            default='kuwaharata@systena.co.jp',
            help='通知先メールアドレス。カンマ区切りで複数指定できます。',
        )
        parser.add_argument(
            '--password-length',
            type=int,
            default=12,
            help='生成するランダムパスワードの長さ。',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には更新・送信せず、内容だけ表示します。',
        )

    def handle(self, *args, **options):
        username = (options.get('username') or 'cs-demo').strip()
        recipient_email = (options.get('recipient_email') or 'kuwaharata@systena.co.jp').strip()
        password_length = options.get('password_length') or 12
        dry_run = options.get('dry_run', False)

        if password_length < 8:
            raise CommandError('password-length は 8 以上を指定してください。')

        User = get_user_model()
        user = User.objects.filter(username=username).select_related('knowledge_profile').first()
        if not user:
            raise CommandError(f'ユーザーが見つかりません: {username}')

        new_password = get_random_string(
            password_length,
            allowed_chars='abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789',
        )
        display_name = resolve_user_display_name(user) or user.username
        recipients = self.parse_email_addresses(recipient_email)

        if not recipients:
            raise CommandError('通知先メールアドレスが指定されていません。')

        subject = '【Tenasapo Knowledge】パスワード変更通知'
        changed_at = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S')
        note_line = f'[{changed_at}] パスワード更新: {new_password}'
        profile = getattr(user, 'knowledge_profile', None)
        note_preview = ''
        if profile:
            existing_note = (profile.note or '').strip()
            note_preview = f'{existing_note}\n{note_line}'.strip() if existing_note else note_line
        body = '\n'.join([
            'パスワードを更新しました。',
            '',
            f'ユーザー名: {display_name}',
            f'ログインID: {user.username}',
            f'新しいパスワード: {new_password}',
            f'変更日時: {changed_at}',
        ])

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'[dry-run] {user.username} のパスワードを更新予定。通知先: {", ".join(recipients)}'
                )
            )
            self.stdout.write(body)
            if profile:
                self.stdout.write('--- 備考欄追記（予定） ---')
                self.stdout.write(note_line)
            else:
                self.stdout.write(
                    self.style.WARNING('knowledge_profile がないため備考欄への追記はスキップされます。')
                )
            return

        if getattr(settings, 'GITHUB_ACTIONS', False):
            self.stdout.write(f'::add-mask::{new_password}')

        user.set_password(new_password)
        user.save(update_fields=['password'])
        if profile:
            profile.note = note_preview
            profile.save(update_fields=['note'])

        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com'),
            recipient_list=recipients,
            fail_silently=False,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'{user.username} のパスワードを更新し、通知メールを送信しました。'
            )
        )
        if profile:
            self.stdout.write(self.style.SUCCESS(f'{user.username} の備考欄を更新しました。'))
        else:
            self.stdout.write(
                self.style.WARNING('knowledge_profile がないため備考欄への追記はスキップしました。')
            )
