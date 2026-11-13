"""Expand existing effective history before switching to exact-month settings.

No initial quota is invented or overwritten. The explicit 1/1/0 provisioning
is a separate, backed-up deployment operation, not a side effect of this migration.
"""

from datetime import date

from django.core.validators import MinValueValidator
from django.db import migrations, models
import tenasapo_knowledge.models


def materialize_months(apps, schema_editor):
    Quota = apps.get_model('tenasapo_knowledge', 'PostingQuota')
    manager = Quota.objects.using(schema_editor.connection.alias)
    history = list(manager.order_by('effective_month'))
    index = 0
    active = None
    month = date(2026, 6, 1)
    while month <= date(2030, 12, 1):
        while index < len(history) and history[index].effective_month <= month:
            active = history[index]
            index += 1
        if active is not None:
            quota, created = manager.get_or_create(effective_month=month, defaults={
                'faq_count': active.faq_count, 'tips_count': active.tips_count,
                'alpha_count': active.alpha_count, 'updated_by_id': active.updated_by_id,
            })
            if created:
                manager.filter(pk=quota.pk).update(updated_at=active.updated_at)
        month = month.replace(year=month.year + 1, month=1) if month.month == 12 else month.replace(month=month.month + 1)


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0078_posting_quota')]

    operations = [
        migrations.AddField(
            model_name='postingquota', name='alpha_count',
            field=models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)], verbose_name='+alpha月間件数'),
        ),
        migrations.RunPython(materialize_months, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='postingquota', name='effective_month',
            field=models.DateField(unique=True, validators=[tenasapo_knowledge.models.validate_quota_month], verbose_name='対象月'),
        ),
    ]