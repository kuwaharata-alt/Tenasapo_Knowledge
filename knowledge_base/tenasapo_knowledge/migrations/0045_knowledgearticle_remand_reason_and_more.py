from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0044_revisionhistory'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='remand_reason',
            field=models.TextField(blank=True, verbose_name='差し戻し理由'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='remand_reason',
            field=models.TextField(blank=True, verbose_name='差し戻し理由'),
        ),
    ]
