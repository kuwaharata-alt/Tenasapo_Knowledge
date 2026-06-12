from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenasapo_knowledge', '0042_userprofile_display_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipsarticle',
            name='view_count',
            field=models.PositiveIntegerField(default=0, verbose_name='閲覧回数'),
        ),
    ]
