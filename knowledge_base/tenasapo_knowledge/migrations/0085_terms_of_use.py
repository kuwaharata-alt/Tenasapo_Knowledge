from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0084_drive_oauth_connection'),
    ]

    operations = [
        migrations.CreateModel(
            name='TermsOfUse',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200, verbose_name='タイトル')),
                ('description', models.TextField(verbose_name='内容')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
            ],
            options={
                'verbose_name': '利用規約',
                'verbose_name_plural': '利用規約',
                'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='terms_of_use_singleton')],
            },
        ),
    ]