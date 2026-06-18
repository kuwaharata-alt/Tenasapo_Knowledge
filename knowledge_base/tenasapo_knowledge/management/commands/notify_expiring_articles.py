from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from tenasapo_knowledge.models import KnowledgeArticle, TipsArticle
from tenasapo_knowledge.utils import resolve_user_display_name


class Command(BaseCommand):
    help = 'FAQ/Tips の掲載期限1週間前に投稿者・承認者へ通知メールを送信します。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際にはメールを送信せず、対象件数のみ表示します。',
        )

    @staticmethod
    def parse_email_addresses(value):
        normalized = value or ''
        for separator in (',', ';', '、'):
            normalized = normalized.replace(separator, '\n')
        return [email.strip() for email in normalized.splitlines() if email.strip()]

    @classmethod
    def collect_user_emails(cls, user):
        if not user:
            return set()

        addresses = set()
        if user.email:
            addresses.add(user.email.strip())

        profile = getattr(user, 'knowledge_profile', None)
        if profile and profile.email_addresses:
            addresses.update(cls.parse_email_addresses(profile.email_addresses))
        return {address for address in addresses if address}

    @staticmethod
    def display_name(saved_name, user):
        name = (saved_name or '').strip()
        if not name and user:
            name = resolve_user_display_name(user)
        return name or '-'

    def send_for_queryset(self, queryset, label, target_date, dry_run=False):
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com')
        sent_count = 0

        for item in queryset:
            recipients = set()
            recipients.update(self.collect_user_emails(item.created_by))
            recipients.update(self.collect_user_emails(item.approved_by))

            if not recipients:
                self.stdout.write(
                    self.style.WARNING(
                        f'通知先メールアドレスなし: {label}「{item.title}」(id={item.id})'
                    )
                )
                continue

            subject = f'【Nexus】{label}「{item.title}」の掲載期限が近づいています'
            body = '\n'.join(
                [
                    f'{label}の掲載期限が1週間後に到来します。',
                    '',
                    f'タイトル: {item.title}',
                    f'カテゴリ: {item.category or "-"}',
                    f'掲載期限: {item.expires_on:%Y-%m-%d}',
                    f'投稿者: {self.display_name(item.created_by_name, item.created_by)}',
                    f'承認者: {self.display_name(item.approved_by_name, item.approved_by)}',
                ]
            )

            if dry_run:
                self.stdout.write(
                    f'[dry-run] {label}「{item.title}」 -> {", ".join(sorted(recipients))}'
                )
            else:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=from_email,
                    recipient_list=sorted(recipients),
                    fail_silently=False,
                )
            sent_count += 1

        return sent_count

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()
        target_date = today + timedelta(days=7)

        faq_queryset = KnowledgeArticle.objects.filter(
            is_published=True,
            expires_on=target_date,
        ).select_related('created_by', 'approved_by')
        tips_queryset = TipsArticle.objects.filter(
            is_published=True,
            expires_on=target_date,
        ).select_related('created_by', 'approved_by')

        faq_count = self.send_for_queryset(faq_queryset, 'FAQ', target_date, dry_run=dry_run)
        tips_count = self.send_for_queryset(tips_queryset, 'Tips', target_date, dry_run=dry_run)

        mode = 'dry-run' if dry_run else 'send'
        self.stdout.write(
            self.style.SUCCESS(
                f'掲載期限通知({mode})完了: FAQ {faq_count}件 / Tips {tips_count}件 (対象日: {target_date:%Y-%m-%d})'
            )
        )
