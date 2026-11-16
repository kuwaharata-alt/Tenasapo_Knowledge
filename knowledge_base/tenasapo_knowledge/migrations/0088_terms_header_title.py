from django.db import migrations, models


def select_legacy_header(apps, schema_editor):
    # Freeze the old exact-match helper, never import changing runtime code.
    # Do not guess from the first record or article-heading text.
    terms = apps.get_model('tenasapo_knowledge', 'TermsOfUse')
    database = schema_editor.connection.alias
    default = 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件'
    for term in terms.objects.using(database).order_by('pk').iterator():
        if term.title.strip() == default:
            terms.objects.using(database).filter(pk=term.pk).update(is_header_title=True)
            break


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0087_multiple_terms')]

    operations = [
        migrations.AddField(
            model_name='termsofuse', name='is_header_title',
            field=models.BooleanField(default=False, verbose_name='タイトルに使用'),
        ),
        migrations.RunPython(select_legacy_header, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='termsofuse',
            constraint=models.UniqueConstraint(
                fields=('is_header_title',), condition=models.Q(is_header_title=True),
                name='terms_one_header_title',
            ),
        ),
    ]