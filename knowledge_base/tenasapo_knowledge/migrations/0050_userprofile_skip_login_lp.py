from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0049_populate_management_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='skip_login_lp',
            field=models.BooleanField(default=False, verbose_name='ログイン後LPを表示しない'),
        ),
    ]
