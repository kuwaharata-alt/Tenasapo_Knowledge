from django.db import migrations


def populate_management_codes(apps, schema_editor):
    KnowledgeArticle = apps.get_model('tenasapo_knowledge', 'KnowledgeArticle')
    TipsArticle = apps.get_model('tenasapo_knowledge', 'TipsArticle')
    ConvenienceFeature = apps.get_model('tenasapo_knowledge', 'ConvenienceFeature')

    for i, article in enumerate(
        KnowledgeArticle.objects.filter(management_code__isnull=True).order_by('created_at', 'id'),
        start=1,
    ):
        article.management_code = f'FQ{i:05d}'
        article.save(update_fields=['management_code'])

    for i, tip in enumerate(
        TipsArticle.objects.filter(management_code__isnull=True).order_by('created_at', 'id'),
        start=1,
    ):
        tip.management_code = f'TP{i:05d}'
        tip.save(update_fields=['management_code'])

    for i, feature in enumerate(
        ConvenienceFeature.objects.filter(management_code__isnull=True).order_by('created_at', 'id'),
        start=1,
    ):
        feature.management_code = f'QR{i:05d}'
        feature.save(update_fields=['management_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0048_management_code'),
    ]

    operations = [
        migrations.RunPython(populate_management_codes, migrations.RunPython.noop),
    ]
