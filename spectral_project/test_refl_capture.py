"""Teste da captura de reflectancia: 1 foto por banda salva numa pasta de teste.

Uso:  python test_refl_capture.py [--stream] [--out NOME]

Sem --stream:  captura com a camera parada (fluxo normal da view).
Com  --stream: abre um gerador MJPEG em outra thread enquanto captura,
               reproduzindo o cenario do botao na pagina (stream ligado).
"""
import os
import sys
import time
import threading
from datetime import datetime

import cv2

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spectral_project.settings")
import django
django.setup()

from django.conf import settings

from spectral_app.camera_functions import ArducamCamera
from spectral_app.views import (LED_BANDS, CAPTURE_DC, _led_set, get_pi)

camera = ArducamCamera()


def fake_stream(seconds):
    """Simula o gerador MJPEG: abre a camera em loop, como a view video_feed."""
    end = time.time() + seconds
    n = 0
    try:
        while time.time() < end:
            with ArducamCamera._lock:
                frame = camera._capture_frame()
            if frame is not None:
                n += 1
            time.sleep(0.01)
    finally:
        with ArducamCamera._lock:
            camera.release()
    print(f"[stream] {n} frames lidos em {seconds}s (liberou a camera)")


def main():
    with_stream = "--stream" in sys.argv
    out_name = None
    if "--out" in sys.argv:
        out_name = sys.argv[sys.argv.index("--out") + 1]

    pi = get_pi()
    if not pi.connected:
        print("ERRO: pigpiod nao conectado. Rode: sudo pigpiod")
        sys.exit(1)
    print("pigpio OK")

    # apaga tudo antes
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)

    if with_stream:
        t = threading.Thread(target=fake_stream, args=(30,), daemon=True)
        t.start()
        time.sleep(1)  # deixa o stream abrir a camera primeiro

    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = out_name or f"refl_test_{now}"
    import os
    out_dir = os.path.join(settings.MEDIA_ROOT, "reflectance", folder)
    os.makedirs(out_dir, exist_ok=True)

    results = {}
    try:
        for nm, pin in LED_BANDS.items():
            _led_set(pi, pin, CAPTURE_DC)
            time.sleep(0.6)  # da tempo do LED subir e da exposicao estabilizar
            frame = camera.grab()
            _led_set(pi, pin, 0)
            time.sleep(0.3)  # espera o LED apagar antes da proxima banda
            if frame is None:
                results[nm] = "SEM FRAME"
                continue
            path = os.path.join(out_dir, f"{nm}nm.png")
            cv2.imwrite(path, frame)
            h, w = frame.shape[:2]
            results[nm] = f"{w}x{h} mean={frame.mean():.1f} max={frame.max()}"
    finally:
        for pin in LED_BANDS.values():
            _led_set(pi, pin, 0)

    print(f"\nPasta: {out_dir}")
    for nm, r in results.items():
        print(f"  {nm}nm -> {r}")
    ok = all(not r.startswith("SEM") and "mean=" in r for r in results.values())
    print("\nRESULTADO:", "TODOS OS FRAMES OK" if ok else "FALHAS ACIMA")


if __name__ == "__main__":
    main()