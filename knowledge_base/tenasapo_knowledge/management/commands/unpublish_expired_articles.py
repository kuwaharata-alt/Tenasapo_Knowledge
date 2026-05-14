from django.core.management.base import BaseCommand
from django.utils import timezone

from tenasapo_knowledge.models import KnowledgeArticle, TipsArticle


class Command(BaseCommand):
    help = '掲載期限が過ぎたFAQ/Tipsを非公開にします。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には更新しません。対象件数のみ表示します。',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.localdate()

        faq_queryset = KnowledgeArticle.objects.filter(
            is_published=True,
            expires_on__lt=today,
        )
        tips_queryset = TipsArticle.objects.filter(
            is_published=True,
            expires_on__lt=today,
        )

        faq_count = 0
        for article in faq_queryset:
            if dry_run:
                self.stdout.write(f'[dry-run] FAQ「{article.title}」(id={article.id}) を非公開化')
            else:
                article.is_published = False
                article.save(update_fields=['is_published', 'updated_at'])
            faq_count += 1

        tips_count = 0
        for tip in tips_queryset:
            if dry_run:
                self.stdout.write(f'[dry-run] Tips「{tip.title}」(id={tip.id}) を非公開化')
            else:
                tip.is_published = False
                tip.save(update_fields=['is_published', 'updated_at'])
            tips_count += 1

        mode = 'dry-run' if dry_run else 'execute'
        self.stdout.write(
            self.style.SUCCESS(
                f'期限切れコンテンツ非公開化({mode})完了: FAQ {faq_count}件 / Tips {tips_count}件'
            )
        )
