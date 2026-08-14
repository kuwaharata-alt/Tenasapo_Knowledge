from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0057_tipsattachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='ai_review',
            field=models.TextField(blank=True, verbose_name='AIレビュー'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='ai_review',
            field=models.TextField(blank=True, verbose_name='AIレビュー'),
        ),
    ]
