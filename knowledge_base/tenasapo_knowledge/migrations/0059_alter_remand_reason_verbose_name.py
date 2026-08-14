from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0058_knowledgearticle_ai_review_and_tipsarticle_ai_review'),
    ]

    operations = [
        migrations.AlterField(
            model_name='knowledgearticle',
            name='remand_reason',
            field=models.TextField(blank=True, verbose_name='差戻し理由'),
        ),
        migrations.AlterField(
            model_name='tipsarticle',
            name='remand_reason',
            field=models.TextField(blank=True, verbose_name='差戻し理由'),
        ),
    ]
