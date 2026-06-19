from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0046_conveniencecategory'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='standard_contract_only',
            field=models.BooleanField(default=False, verbose_name='テナサポStandard契約者限定'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='standard_contract_only',
            field=models.BooleanField(default=False, verbose_name='テナサポStandard契約者限定'),
        ),
    ]
