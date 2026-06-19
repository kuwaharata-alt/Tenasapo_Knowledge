from django.db import migrations, models


def seed_convenience_categories(apps, schema_editor):
    ConvenienceCategory = apps.get_model('tenasapo_knowledge', 'ConvenienceCategory')
    rows = [
        ('shortcut', 'Winodws', 'デスクトップ'),
        ('shortcut', 'Winodws', '文書'),
        ('shortcut', 'Winodws', 'ウィンドウ'),
        ('shortcut', 'Winodws', 'ファイル操作'),
        ('shortcut', 'Office', 'Word'),
        ('shortcut', 'Office', 'Excel'),
        ('shortcut', 'Office', 'PowerPoint'),
        ('shortcut', 'Office', 'Outlook'),
        ('shortcut', 'Googleカレンダー', '基本操作'),
        ('shortcut', 'Googleカレンダー', '予定管理'),
        ('shortcut', 'コントロールパネル', '基本操作'),
        ('shortcut', 'コントロールパネル', 'プログラム'),
        ('shortcut', 'コントロールパネル', 'システム'),
        ('shortcut', 'Windowsの設定', '基本操作'),
        ('shortcut', 'Windowsの設定', 'ネットワーク'),
        ('shortcut', 'Windowsの設定', 'アカウント'),
        ('command', 'Winodws', 'ファイル操作'),
        ('command', 'Winodws', 'ネットワーク'),
        ('command', 'Winodws', 'システム'),
        ('command', 'Office', 'Word'),
        ('command', 'Office', 'Excel'),
        ('command', 'Office', 'PowerPoint'),
        ('command', 'Office', 'Outlook'),
    ]
    for reference_type, category, middle_category in rows:
        ConvenienceCategory.objects.get_or_create(
            reference_type=reference_type,
            category=category,
            middle_category=middle_category,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0045_knowledgearticle_remand_reason_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConvenienceCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_type', models.CharField(choices=[('shortcut', 'ショートカット'), ('command', 'コマンド')], max_length=20, verbose_name='大カテゴリ')),
                ('category', models.CharField(max_length=120, verbose_name='中カテゴリ')),
                ('middle_category', models.CharField(blank=True, default='', max_length=120, verbose_name='小カテゴリ')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
            ],
            options={
                'verbose_name': 'QRカテゴリ',
                'verbose_name_plural': 'QRカテゴリ',
                'ordering': ['reference_type', 'category', 'middle_category', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='conveniencecategory',
            constraint=models.UniqueConstraint(fields=('reference_type', 'category', 'middle_category'), name='unique_qr_category_path'),
        ),
        migrations.RunPython(seed_convenience_categories, migrations.RunPython.noop),
    ]
