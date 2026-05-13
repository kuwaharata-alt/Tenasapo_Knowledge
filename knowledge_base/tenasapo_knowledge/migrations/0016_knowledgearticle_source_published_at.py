from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0015_knowledgearticle_is_approved'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='source_published_at',
            field=models.DateField(blank=True, null=True, verbose_name='ソース公開日'),
        ),
    ]
