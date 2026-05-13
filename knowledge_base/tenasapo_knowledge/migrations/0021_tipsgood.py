from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0020_userprofile_user_id'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TipsGood',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('tip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='goods', to='tenasapo_knowledge.tipsarticle', verbose_name='Tips')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tips_goods', to=settings.AUTH_USER_MODEL, verbose_name='ユーザー')),
            ],
            options={
                'verbose_name': 'Tipsグッド',
                'verbose_name_plural': 'Tipsグッド',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='tipsgood',
            constraint=models.UniqueConstraint(fields=('tip', 'user'), name='unique_tip_good_per_user'),
        ),
    ]