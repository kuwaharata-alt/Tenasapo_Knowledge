from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0073_knowledgearticle_reviewer_comment_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='manual',
            name='pdf_file',
            field=models.FileField(
                blank=True, upload_to='manuals/%Y/%m/', verbose_name='PDFファイル',
            ),
        ),
    ]