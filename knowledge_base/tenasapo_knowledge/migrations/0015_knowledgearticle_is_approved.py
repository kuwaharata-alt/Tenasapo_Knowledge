from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0014_knowledgearticle_created_by_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgearticle',
            name='is_approved',
            field=models.BooleanField(default=True, verbose_name='承認済み'),
        ),
    ]
