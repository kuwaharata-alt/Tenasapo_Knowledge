from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0074_alter_manual_pdf_file')]

    operations = [
        migrations.AddField(
            model_name='manual', name='menu_key',
            field=models.CharField(
                'メニュー識別子', max_length=64, null=True, blank=True,
                unique=True, editable=False,
            ),
        ),
    ]