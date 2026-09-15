from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver

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
