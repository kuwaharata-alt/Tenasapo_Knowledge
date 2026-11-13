from django.core.management.base import BaseCommand
from django.db import transaction
from django.urls import reverse

from tenasapo_knowledge.manual_defaults import OPERATION_MANUALS
from tenasapo_knowledge.models import Manual


class Command(BaseCommand):
    help = 'メニュー別運用手順の不足分だけを登録します。既存本文・設定・ファイルは変更しません。'

    @transaction.atomic
    def handle(self, *args, **options):
        for section in OPERATION_MANUALS:
            reverse(section.url_name)
        created_count = 0
        for order, section in enumerate(OPERATION_MANUALS):
            _, created = Manual.objects.get_or_create(
                menu_key=section.menu_key,
                defaults={
                    'title': section.title, 'description': section.description,
                    'order': order, 'is_published': True,
                },
            )
            created_count += created
        self.stdout.write(self.style.SUCCESS(
            f'追加 {created_count} 件 / 既存 {len(OPERATION_MANUALS) - created_count} 件（変更なし）'
        ))