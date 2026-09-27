from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('spectral_app', '0030_assign_project_owners'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='projects', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='project',
            unique_together={('owner', 'name')},
        ),
    ]
