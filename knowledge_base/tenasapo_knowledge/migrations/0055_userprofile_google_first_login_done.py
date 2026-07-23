from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0054_loginhistory_auth_account_email_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='google_first_login_done',
            field=models.BooleanField(
                default=True,
                help_text='Google認証で初回ログインした際の初回登録フォーム完了フラグ。False=フォーム表示が必要。',
                verbose_name='Google初回ログイン登録完了',
            ),
        ),
    ]
