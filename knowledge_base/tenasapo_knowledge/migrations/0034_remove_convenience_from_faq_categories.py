from django.db import migrations


def remove_convenience_categories(apps, schema_editor):
    FAQCategory = apps.get_model('tenasapo_knowledge', 'FAQCategory')
    FAQParentCategorySetting = apps.get_model('tenasapo_knowledge', 'FAQParentCategorySetting')

    FAQCategory.objects.filter(parent_name='便利機能').delete()
    FAQParentCategorySetting.objects.filter(name='便利機能').delete()


def restore_convenience_categories(apps, schema_editor):
    FAQCategory = apps.get_model('tenasapo_knowledge', 'FAQCategory')

    categories = [
        ('便利機能', 'ショートカット', 'Winodws'),
        ('便利機能', 'ショートカット', 'Office'),
        ('便利機能', 'ショートカット', 'Googleカレンダー'),
        ('便利機能', 'ショートカット', 'ファイル名を指定して実行'),
    ]

    for parent_name, middle_name, child_name in categories:
        FAQCategory.objects.get_or_create(
            parent_name=parent_name,
            middle_name=middle_name,
            child_name=child_name,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0033_add_shortcut_categories'),
    ]

    operations = [
        migrations.RunPython(
            remove_convenience_categories,
            restore_convenience_categories,
        ),
    ]
