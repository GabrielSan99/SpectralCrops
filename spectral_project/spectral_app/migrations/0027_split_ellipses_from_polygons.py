# Le poly_kinds (ainda existe nesse ponto -- so e removido na proxima
# migracao) pra separar o que era elipse do que era poligono de verdade,
# movendo elipse pro campo novo `ellipses`/`ellipse_labels` em vez de deixar
# junto com `polygons`/`poly_labels`. Anotacao mais antiga, salva antes até
# do poly_kinds existir, cai no mesmo fallback por contagem de pontos usado
# em views.py (_classify_polygon_shape/ANNOTATE_ELLIPSE_PTS).

from django.db import migrations

ANNOTATE_ELLIPSE_PTS = 32


def split_ellipses(apps, schema_editor):
    Annotation = apps.get_model('spectral_app', 'Annotation')
    for ann in Annotation.objects.all():
        polygons = ann.polygons or []
        poly_labels = ann.poly_labels or [""] * len(polygons)
        poly_kinds = ann.poly_kinds or []

        kept_polygons, kept_labels = [], []
        new_ellipses, new_ellipse_labels = [], []
        for i, (pts, lbl) in enumerate(zip(polygons, poly_labels)):
            kind = poly_kinds[i] if i < len(poly_kinds) else None
            if kind not in ("ellipse", "polygon"):
                kind = "ellipse" if len(pts) == ANNOTATE_ELLIPSE_PTS else "polygon"
            if kind == "ellipse":
                new_ellipses.append(pts)
                new_ellipse_labels.append(lbl)
            else:
                kept_polygons.append(pts)
                kept_labels.append(lbl)

        if new_ellipses:
            ann.polygons = kept_polygons
            ann.poly_labels = kept_labels
            ann.ellipses = new_ellipses
            ann.ellipse_labels = new_ellipse_labels
            ann.save(update_fields=['polygons', 'poly_labels', 'ellipses', 'ellipse_labels'])


def noop_reverse(apps, schema_editor):
    pass  # nao da pra saber que ordem original tinha entre polygons/ellipses -- reverso e um no-op


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0026_annotation_add_ellipses'),
    ]

    operations = [
        migrations.RunPython(split_ellipses, noop_reverse),
    ]
