from django.core.management.color import no_style
from django.db import migrations, models


def reset_terms_sequence(apps, schema_editor):
    # PostgreSQL's new identity sequence must start AFTER the retained fixed PK.
    # SQLite's rebuilt INTEGER PRIMARY KEY automatically uses the existing max.
    terms = apps.get_model('tenasapo_knowledge', 'TermsOfUse')
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [terms]):
        schema_editor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0086_terms_acceptance')]

    operations = [
        migrations.RemoveConstraint(model_name='termsofuse', name='terms_of_use_singleton'),
        migrations.AlterField(model_name='termsofuse', name='id',
                              field=models.BigAutoField(primary_key=True, serialize=False)),
        migrations.AlterModelOptions(name='termsofuse', options={
            'verbose_name': '利用規約', 'verbose_name_plural': '利用規約', 'ordering': ['pk'],
        }),
        migrations.RunPython(reset_terms_sequence, migrations.RunPython.noop),
        # Do not backfill from live terms: those may differ from acknowledged terms.
        # Legacy columns remain the authoritative single-document snapshot.
        migrations.AddField(model_name='termsacceptance', name='terms_snapshots',
                            field=models.JSONField(blank=True, default=list, editable=False,
                                                   verbose_name='同意した規約一覧')),
    ]