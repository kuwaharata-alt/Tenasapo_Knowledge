from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0070_userprofile_demo_account_expires_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='visible_to_demo',
            field=models.BooleanField(default=False, verbose_name='デモユーザー向け表示'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='visible_to_demo',
            field=models.BooleanField(default=False, verbose_name='デモユーザー向け表示'),
        ),
    ]
