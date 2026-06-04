from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0027_faqparentcategorysetting'),
    ]

    operations = [
        migrations.CreateModel(
            name='TipsImageAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='tips_attachments/%Y/%m/', verbose_name='画像ファイル')),
                ('display_name', models.CharField(blank=True, max_length=200, verbose_name='表示名')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, verbose_name='アップロード日時')),
                ('tip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='tenasapo_knowledge.tipsarticle', verbose_name='Tips')),
            ],
            options={
                'verbose_name': 'Tips画像',
                'verbose_name_plural': 'Tips画像',
                'ordering': ['uploaded_at', 'id'],
            },
        ),
    ]
