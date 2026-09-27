# poly_kinds ja cumpriu o papel dele (separar elipse de poligono na
# migracao anterior) -- a distincao agora mora em campos separados
# (polygons vs ellipses), entao esse campo intermediario nao serve mais.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0027_split_ellipses_from_polygons'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='annotation',
            name='poly_kinds',
        ),
    ]
