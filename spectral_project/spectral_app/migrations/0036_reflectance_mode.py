from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0035_annotation_image_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='refl_mode',
            field=models.CharField(choices=[('mean', 'Média da região'), ('pixel', 'Pixel a pixel')],
                                   default='mean', max_length=10),
        ),
        migrations.AddField(
            model_name='reflectancecalibration',
            name='frames_dir',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='reflectancezerocalibration',
            name='frames_dir',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
