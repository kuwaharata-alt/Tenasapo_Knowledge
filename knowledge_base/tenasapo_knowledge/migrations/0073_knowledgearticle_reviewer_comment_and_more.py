from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0072_alter_faqcategory_middle_name_and_child_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='reviewer_comment',
            field=models.TextField(blank=True, default='', verbose_name='承認者コメント'),
        ),
        migrations.AddField(
            model_name='tipsarticle',
            name='reviewer_comment',
            field=models.TextField(blank=True, default='', verbose_name='承認者コメント'),
        ),
    ]