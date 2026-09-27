import os
import shutil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from .models import (DataAcquisition, GeometricCalibration, Project,
                     ReflectanceCalibration, ReflectanceZeroCalibration)

DEFAULT_SUPERUSER = "Admin"
DEFAULT_SUPERUSER_PASSWORD = "Spectral123"


@receiver(post_migrate)
def create_default_superuser(sender, **kwargs):
    """Garante um superusuario padrao logo apos o primeiro migrate -- sem
    isso, um banco recem-criado (ex.: depois de zerar o db.sqlite3) fica sem
    ninguem capaz de logar, ja que toda a app exige @login_required."""
    if sender.name != "spectral_app":
        return
    User = get_user_model()
    if User.objects.filter(username=DEFAULT_SUPERUSER).exists():
        return
    User.objects.create_superuser(username=DEFAULT_SUPERUSER, email="",
                                  password=DEFAULT_SUPERUSER_PASSWORD)
    print(f"Superusuário padrão criado: {DEFAULT_SUPERUSER} / {DEFAULT_SUPERUSER_PASSWORD} "
          f"(troque a senha depois do primeiro login).")


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_project_for_new_user(sender, instance, created, **kwargs):
    """Todo usuario novo (menos o Admin, que ja fica dono do "Projeto
    inicial" com as capturas reais -- ver migration 0030) nasce com seu
    proprio "Projeto Inicial" pronto pra usar, com parametros de filtro/LED
    padrao ja preenchidos (_ensure_param_rows) -- sem isso a Parametrizacao
    chegaria vazia e o usuario precisaria descobrir sozinho os defaults de
    fabrica. Import de views.py fica local (nao no topo do arquivo) pra
    evitar import circular no startup do app (views.py tambem importa
    coisa de signals.py/models.py)."""
    if not created or instance.username == DEFAULT_SUPERUSER:
        return
    if Project.objects.filter(owner=instance).exists():
        return
    from .views import _ensure_param_rows
    project = Project.objects.create(owner=instance, name="Projeto Inicial", is_default=True)
    _ensure_param_rows(project)


@receiver(post_delete, sender=DataAcquisition)
def delete_acquisition_folder(sender, instance, **kwargs):
    """Apaga a pasta de imagens (raw/normalized/rgb) do disco junto com o
    registro -- dispara tanto num delete direto (data_acquisition_delete)
    quanto num CASCADE vindo da exclusao do projeto (Project.on_delete nos
    campos de calibracao/aquisicao agora e CASCADE, nao SET_NULL -- sem
    isso as imagens ficavam orfas em disco, sem nenhuma tela pra alcancar
    ou apagar elas depois)."""
    if not instance.folder:
        return
    folder_path = os.path.join(settings.MEDIA_ROOT, "acquisitions", instance.folder)
    shutil.rmtree(folder_path, ignore_errors=True)


def _delete_calibration_image(sender, instance, **kwargs):
    """Apaga o arquivo de imagem (foto de referencia) da calibracao junto
    com o registro -- mesmo motivo do signal acima."""
    if instance.image:
        instance.image.delete(save=False)


post_delete.connect(_delete_calibration_image, sender=GeometricCalibration)
post_delete.connect(_delete_calibration_image, sender=ReflectanceCalibration)
post_delete.connect(_delete_calibration_image, sender=ReflectanceZeroCalibration)
