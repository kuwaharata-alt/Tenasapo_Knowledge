from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0079_monthly_posting_quota_alpha')]

    operations = [
        migrations.AlterField(
            model_name='postingquota', name='alpha_count',
            field=models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)], verbose_name='＋α月間件数'),
        ),
    ]