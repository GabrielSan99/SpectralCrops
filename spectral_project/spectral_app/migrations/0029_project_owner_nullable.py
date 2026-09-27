from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('spectral_app', '0028_remove_annotation_poly_kinds'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='name',
            field=models.CharField(max_length=200),
        ),
        migrations.AddField(
            model_name='project',
            name='owner',
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='projects', to=settings.AUTH_USER_MODEL),
        ),
    ]
