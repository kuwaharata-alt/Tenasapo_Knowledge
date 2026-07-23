from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0055_userprofile_google_first_login_done'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loginhistory',
            name='auth_provider',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='認証プロバイダ'),
        ),
        migrations.AlterField(
            model_name='loginhistory',
            name='auth_account_email',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='認証アカウントメール'),
        ),
        migrations.AlterField(
            model_name='loginhistory',
            name='auth_account_uid',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='認証アカウントUID'),
        ),
    ]
