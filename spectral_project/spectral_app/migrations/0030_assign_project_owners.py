from django.conf import settings
from django.db import migrations

# Inlined (nao importar views.py em migration): mesmos valores de
# FILTER_DEFAULTS/LED_BANDS/BAND_DEFAULT_INTENSITY de views.py, usados pra
# dar uma base ja pronta ao "Projeto Inicial" auto-criado de cada usuario.
FILTER_DEFAULTS = {
    1: {"name": "Vazio", "steps": -125},
    2: {"name": "Passa Alta 650nm", "steps": -500},
    3: {"name": "Passa Alta 670nm", "steps": -875},
    4: {"name": "Passa Alta 720nm", "steps": -1250},
}
LED_BANDS = ["365", "400", "460", "520", "590", "660", "730", "850"]
BAND_DEFAULT_INTENSITY = 255

DEFAULT_SUPERUSER = "Admin"


def assign_owners(apps, schema_editor):
    Project = apps.get_model('spectral_app', 'Project')
    FilterPosition = apps.get_model('spectral_app', 'FilterPosition')
    BandParameter = apps.get_model('spectral_app', 'BandParameter')
    User = apps.get_model(settings.AUTH_USER_MODEL)

    admin = User.objects.filter(username=DEFAULT_SUPERUSER).first()
    if admin:
        # Projeto(s) pre-existente(s) sem dono (ex.: "Projeto inicial" com as
        # capturas reais ja feitas) ficam com o Admin -- decisao explicita do
        # usuario: nao reatribuir pra quem esta rodando a migracao, e sim pro
        # superusuario padrao do sistema.
        Project.objects.filter(owner__isnull=True).update(owner=admin)

    for user in User.objects.all():
        if Project.objects.filter(owner=user).exists():
            continue
        project = Project.objects.create(owner=user, name="Projeto Inicial", is_default=True)
        for i in range(1, 7):
            defaults = FILTER_DEFAULTS.get(i, {})
            FilterPosition.objects.get_or_create(
                project=project, index=i,
                defaults={"name": defaults.get("name", ""), "steps": defaults.get("steps", 0)},
            )
        for order, nm in enumerate(LED_BANDS):
            BandParameter.objects.get_or_create(
                project=project, nm=nm,
                defaults={"order": order, "intensity": BAND_DEFAULT_INTENSITY},
            )


def noop_reverse(apps, schema_editor):
    pass  # nao da pra saber com seguranca quais projetos eram "orfaos" antes


class Migration(migrations.Migration):

    dependencies = [
        ('spectral_app', '0029_project_owner_nullable'),
    ]

    operations = [
        migrations.RunPython(assign_owners, noop_reverse),
    ]
