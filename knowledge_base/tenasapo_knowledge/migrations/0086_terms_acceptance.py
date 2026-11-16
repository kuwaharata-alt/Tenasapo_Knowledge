from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0085_terms_of_use'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TermsAcceptance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accepted_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='同意日時')),
                ('terms_updated_at', models.DateTimeField(editable=False, verbose_name='同意した規約の更新日時')),
                ('terms_title', models.CharField(editable=False, max_length=200, verbose_name='同意したタイトル')),
                ('terms_description', models.TextField(editable=False, verbose_name='同意した内容')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='terms_acceptance', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': '利用規約への同意', 'verbose_name_plural': '利用規約への同意'},
        ),
    ]