from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0069_alter_userprofile_exclude_from_analysis'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='demo_account_expires_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='デモアカウント有効期限'),
        ),
    ]
