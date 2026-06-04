from django.db import migrations


def add_cloud_and_groupware_categories(apps, schema_editor):
    FAQCategory = apps.get_model('tenasapo_knowledge', 'FAQCategory')

    categories = [
        ('クラウドインフラ', '', 'Azure'),
        ('クラウドインフラ', '', 'AWS'),
        ('クラウドインフラ', '', 'Google Cloud'),
        ('クラウドインフラ', '', 'OCI'),
        ('グループウェア', '', 'Google Workspace'),
        ('グループウェア', '', 'M365'),
        ('グループウェア', '', 'LINE WORKS'),
        ('グループウェア', '', 'サイボウズ'),
        ('グループウェア', '', "desknet's NEO"),
    ]

    for parent_name, middle_name, child_name in categories:
        FAQCategory.objects.get_or_create(
            parent_name=parent_name,
            middle_name=middle_name,
            child_name=child_name,
        )


def remove_cloud_and_groupware_categories(apps, schema_editor):
    FAQCategory = apps.get_model('tenasapo_knowledge', 'FAQCategory')

    targets = {
        ('クラウドインフラ', '', 'Azure'),
        ('クラウドインフラ', '', 'AWS'),
        ('クラウドインフラ', '', 'Google Cloud'),
        ('クラウドインフラ', '', 'OCI'),
        ('グループウェア', '', 'Google Workspace'),
        ('グループウェア', '', 'M365'),
        ('グループウェア', '', 'LINE WORKS'),
        ('グループウェア', '', 'サイボウズ'),
        ('グループウェア', '', "desknet's NEO"),
    }

    for parent_name, middle_name, child_name in targets:
        FAQCategory.objects.filter(
            parent_name=parent_name,
            middle_name=middle_name,
            child_name=child_name,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0030_knowledgearticle_target_os'),
    ]

    operations = [
        migrations.RunPython(
            add_cloud_and_groupware_categories,
            remove_cloud_and_groupware_categories,
        ),
    ]
