from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0071_knowledgearticle_visible_to_demo_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='faqcategory',
            name='middle_name',
            field=models.CharField(default='', max_length=120, verbose_name='中カテゴリ'),
        ),
        migrations.AlterField(
            model_name='faqcategory',
            name='child_name',
            field=models.CharField(blank=True, default='', max_length=120, verbose_name='小カテゴリ'),
        ),
    ]