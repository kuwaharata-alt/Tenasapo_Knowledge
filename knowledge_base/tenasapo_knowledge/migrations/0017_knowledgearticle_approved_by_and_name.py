from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0016_knowledgearticle_source_published_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='approved_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='approved_knowledge_articles',
                to=settings.AUTH_USER_MODEL,
                verbose_name='承認者',
            ),
        ),
        migrations.AddField(
            model_name='knowledgearticle',
            name='approved_by_name',
            field=models.CharField(blank=True, max_length=150, verbose_name='承認者名'),
        ),
    ]
