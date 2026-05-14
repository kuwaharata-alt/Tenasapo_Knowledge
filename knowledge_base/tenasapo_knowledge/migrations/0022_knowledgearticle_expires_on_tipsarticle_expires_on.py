from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0021_tipsgood'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='expires_on',
            field=models.DateField(blank=True, null=True, verbose_name='掲載期限'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='expires_on',
            field=models.DateField(blank=True, null=True, verbose_name='掲載期限'),
        ),
    ]
