from django.db import migrations, models


def copy_notes(apps, schema_editor):
    Project = apps.get_model('spectral_app', 'Project')
    Note = apps.get_model('spectral_app', 'ParameterizationNote')
    for note in Note.objects.order_by('created'):
        Project.objects.filter(id=note.project_id).update(param_note=note.text)


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0033_parameterization_note_updated'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='param_note',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunPython(copy_notes, migrations.RunPython.noop),
        migrations.DeleteModel(name='ParameterizationNote'),
    ]
