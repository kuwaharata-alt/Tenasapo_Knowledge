from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0075_manual_menu_key')]

    operations = [
        migrations.AlterModelOptions(
            name='faqcategory',
            options={
                'ordering': ['parent_name', 'middle_name', 'child_name'],
                'verbose_name': 'QAカテゴリ',
                'verbose_name_plural': 'QAカテゴリ',
            },
        ),
        migrations.AlterModelOptions(
            name='faqparentcategorysetting',
            options={
                'ordering': ['name'],
                'verbose_name': 'QA大カテゴリ設定',
                'verbose_name_plural': 'QA大カテゴリ設定',
            },
        ),
        migrations.AlterModelOptions(
            name='articlegood',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'QAグッド',
                'verbose_name_plural': 'QAグッド',
            },
        ),
        migrations.AlterModelOptions(
            name='articlefavorite',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'QAお気に入り',
                'verbose_name_plural': 'QAお気に入り',
            },
        ),
    ]