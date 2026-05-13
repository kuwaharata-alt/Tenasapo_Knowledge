# Generated manually 2026-05-13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0019_tipsarticle_pdf_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='uid',
            field=models.CharField(
                blank=True,
                help_text='数字6桁',
                max_length=6,
                null=True,
                unique=True,
                verbose_name='ユーザーID',
            ),
        ),
    ]
