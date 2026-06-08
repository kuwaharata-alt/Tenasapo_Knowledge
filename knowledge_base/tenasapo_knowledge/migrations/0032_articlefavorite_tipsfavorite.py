from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0031_add_cloud_and_groupware_categories'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArticleFavorite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('article', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='favorites', to='tenasapo_knowledge.knowledgearticle', verbose_name='記事')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='article_favorites', to=settings.AUTH_USER_MODEL, verbose_name='ユーザー')),
            ],
            options={
                'verbose_name': 'FAQお気に入り',
                'verbose_name_plural': 'FAQお気に入り',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TipsFavorite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('tip', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='favorites', to='tenasapo_knowledge.tipsarticle', verbose_name='Tips')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='tips_favorites', to=settings.AUTH_USER_MODEL, verbose_name='ユーザー')),
            ],
            options={
                'verbose_name': 'Tipsお気に入り',
                'verbose_name_plural': 'Tipsお気に入り',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='articlefavorite',
            constraint=models.UniqueConstraint(fields=('article', 'user'), name='unique_article_favorite_per_user'),
        ),
        migrations.AddConstraint(
            model_name='tipsfavorite',
            constraint=models.UniqueConstraint(fields=('tip', 'user'), name='unique_tip_favorite_per_user'),
        ),
    ]
