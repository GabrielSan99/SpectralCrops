"""Renomeia a pasta das bandas calibradas de cada aquisição: normalized/ -> calibrated/.

Uso (dentro de spectral_project/):
    python manage.py rename_calibrated_dirs            # só mostra o que faria
    python manage.py rename_calibrated_dirs --aplicar  # renomeia de verdade

O app já lê as duas (ver views.CALIBRATED_DIR / LEGACY_CALIBRATED_DIR), então
rodar isso é só pra deixar o disco com um nome só -- nada quebra se não rodar.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from spectral_app.views import CALIBRATED_DIR, LEGACY_CALIBRATED_DIR


class Command(BaseCommand):
    help = "Renomeia normalized/ -> calibrated/ em todas as pastas de aquisição."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true",
                            help="Renomeia de verdade (sem isso, só lista o que faria).")

    def handle(self, *args, **opts):
        base = os.path.join(settings.MEDIA_ROOT, "acquisitions")
        if not os.path.isdir(base):
            self.stdout.write("Nenhuma pasta de aquisições encontrada.")
            return
        renamed = skipped = conflicts = 0
        for folder in sorted(os.listdir(base)):
            old = os.path.join(base, folder, LEGACY_CALIBRATED_DIR)
            new = os.path.join(base, folder, CALIBRATED_DIR)
            if not os.path.isdir(old):
                skipped += 1
                continue
            if os.path.exists(new):   # as duas existem: não mexe, pra não misturar/perder nada
                conflicts += 1
                self.stdout.write(self.style.WARNING(f"  CONFLITO (as duas pastas existem, não mexi): {folder}"))
                continue
            if opts["aplicar"]:
                os.rename(old, new)
            renamed += 1
        verb = "renomeadas" if opts["aplicar"] else "seriam renomeadas (rode com --aplicar)"
        self.stdout.write(self.style.SUCCESS(
            f"{renamed} pasta(s) {verb} | {skipped} sem {LEGACY_CALIBRATED_DIR}/ | {conflicts} conflito(s)"))
