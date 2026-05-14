from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0023_alter_knowledgearticle_expires_on_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipsarticle',
            name='source_published_at',
            field=models.DateField(blank=True, null=True, verbose_name='ソース公開日'),
        ),
    ]
