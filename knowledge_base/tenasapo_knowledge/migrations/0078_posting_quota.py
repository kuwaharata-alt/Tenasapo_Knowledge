import datetime

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion
import tenasapo_knowledge.models


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0077_q_and_a_display_labels'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PostingQuota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_month', models.DateField(unique=True, validators=[tenasapo_knowledge.models.validate_quota_month], verbose_name='適用開始月')),
                ('faq_count', models.PositiveIntegerField(validators=[MinValueValidator(0)], verbose_name='Q&A月間件数')),
                ('tips_count', models.PositiveIntegerField(validators=[MinValueValidator(0)], verbose_name='Tips月間件数')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新日時')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='更新者')),
            ],
            options={
                'ordering': ['effective_month'],
                'verbose_name': '投稿目標',
                'verbose_name_plural': '投稿目標',
                'constraints': [models.CheckConstraint(
                    condition=models.Q(effective_month__gte=datetime.date(2026, 6, 1), effective_month__day=1),
                    name='posting_quota_valid_month',
                )],
            },
        ),
    ]