from django.db import migrations, models
import tenasapo_knowledge.models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0022_knowledgearticle_expires_on_tipsarticle_expires_on'),
    ]

    operations = [
        migrations.AlterField(
            model_name='knowledgearticle',
            name='expires_on',
            field=models.DateField(blank=True, default=tenasapo_knowledge.models.default_expires_on, null=True, verbose_name='掲載期限'),
        ),
        migrations.AlterField(
            model_name='tipsarticle',
            name='expires_on',
            field=models.DateField(blank=True, default=tenasapo_knowledge.models.default_expires_on, null=True, verbose_name='掲載期限'),
        ),
    ]
