from django.apps import AppConfig


class SpectralAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'spectral_app'

    def ready(self):
        from . import signals  # noqa: F401 -- conecta o @receiver post_migrate
