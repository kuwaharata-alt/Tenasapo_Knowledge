from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0076_qa_display_labels')]

    operations = [
        migrations.AlterModelOptions(
            name='faqcategory',
            options={
                'ordering': ['parent_name', 'middle_name', 'child_name'],
                'verbose_name': 'Q&Aカテゴリ',
                'verbose_name_plural': 'Q&Aカテゴリ',
            },
        ),
        migrations.AlterModelOptions(
            name='faqparentcategorysetting',
            options={
                'ordering': ['name'],
                'verbose_name': 'Q&A大カテゴリ設定',
                'verbose_name_plural': 'Q&A大カテゴリ設定',
            },
        ),
        migrations.AlterModelOptions(
            name='articlegood',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Q&Aグッド',
                'verbose_name_plural': 'Q&Aグッド',
            },
        ),
        migrations.AlterModelOptions(
            name='articlefavorite',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Q&Aお気に入り',
                'verbose_name_plural': 'Q&Aお気に入り',
            },
        ),
    ]