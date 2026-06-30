from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0050_userprofile_skip_login_lp'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='tags',
            field=models.CharField(blank=True, max_length=300, verbose_name='タグ'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='tags',
            field=models.CharField(blank=True, max_length=300, verbose_name='タグ'),
        ),
    ]
