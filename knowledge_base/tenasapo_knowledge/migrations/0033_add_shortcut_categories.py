from django.db import migrations


def add_shortcut_categories(apps, schema_editor):
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


def remove_shortcut_categories(apps, schema_editor):
    FAQCategory = apps.get_model('tenasapo_knowledge', 'FAQCategory')

    targets = {
        ('便利機能', 'ショートカット', 'Winodws'),
        ('便利機能', 'ショートカット', 'Office'),
        ('便利機能', 'ショートカット', 'Googleカレンダー'),
        ('便利機能', 'ショートカット', 'ファイル名を指定して実行'),
    }

    for parent_name, middle_name, child_name in targets:
        FAQCategory.objects.filter(
            parent_name=parent_name,
            middle_name=middle_name,
            child_name=child_name,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0032_articlefavorite_tipsfavorite'),
    ]

    operations = [
        migrations.RunPython(
            add_shortcut_categories,
            remove_shortcut_categories,
        ),
    ]
