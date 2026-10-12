from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0034_project_param_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='annotation',
            name='image_note',
            field=models.TextField(blank=True, default=''),
        ),
    ]
