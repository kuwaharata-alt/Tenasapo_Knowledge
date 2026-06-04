from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0026_alter_faqcategory_options_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='FAQParentCategorySetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True, verbose_name='大カテゴリ名')),
                ('visible_to_customer', models.BooleanField(default=True, verbose_name='カスタマーユーザーに表示')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
            ],
            options={
                'verbose_name': 'FAQ大カテゴリ設定',
                'verbose_name_plural': 'FAQ大カテゴリ設定',
                'ordering': ['name'],
            },
        ),
    ]
