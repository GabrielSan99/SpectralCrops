from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse, JsonResponse
from django.core.files.base import ContentFile
from django.conf import settings

import pigpio
from .camera_functions import ArducamCamera
from .models import (FilterPosition, BandParameter, GeometricCalibration,
                     ReflectanceCalibration, DEFAULT_FILTER_NAME)
import os
from datetime import datetime
import base64
import json
import re
import threading
import time
import cv2
import numpy as np

camera = ArducamCamera()

# Ha uma unica camera UVC. O browser pode levar alguns instantes para encerrar
# uma conexao MJPEG ao remover o src da tag <img>; sem este controle, um Start
# imediato cria um segundo leitor de /dev/videoN e a Arducam para de responder.
_stream_state_lock = threading.Lock()
_active_stream = None
STREAM_STOP_TIMEOUT = 3.0


def _stop_active_stream():
    """Solicita o fim do stream atual e espera a camera ser liberada."""
    with _stream_state_lock:
        state = _active_stream
    if state is None:
        return True
    state["stop"].set()
    return state["finished"].wait(STREAM_STOP_TIMEOUT)


def _controlled_stream(state):
    """Envolve o gerador da camera e publica quando o release terminou."""
    global _active_stream
    try:
        yield from camera.stream_frames(state["stop"])
    finally:
        state["finished"].set()
        with _stream_state_lock:
            if _active_stream is state:
                _active_stream = None

# ─────────────────────────────────────────────────────────────
# pigpio compartilhado (uma conexao reaproveitada entre requisicoes)
# ─────────────────────────────────────────────────────────────
_pi = pigpio.pi()


def get_pi():
    """Retorna a conexao pigpio, reconectando se caiu (ex.: pigpiod reiniciou).

    O atributo .connected nao detecta um socket quebrado, entao verificamos com
    uma chamada barata; se falhar, refazemos a conexao e reconfiguramos os pinos.
    """
    global _pi, _gpio_ready
    ok = _pi is not None and _pi.connected
    if ok:
        try:
            _pi.get_pigpio_version()   # ping no daemon
        except Exception:
            ok = False
    if not ok:
        try:
            if _pi is not None:
                _pi.stop()
        except Exception:
            pass
        _pi = pigpio.pi()
        _gpio_ready = False            # forca reconfiguracao dos pinos
    _ensure_gpio(_pi)
    return _pi


# ─────────────────────────────────────────────────────────────
# LEDs — 8 canais de alto brilho (drivers PT4115). PWM por software.
# GPIO -> comprimento de onda (nm). 365 no GPIO16 (movido do 18 especial).
# DIM do PT4115: dutycycle 0 = apagado, 255 = brilho maximo.
# ─────────────────────────────────────────────────────────────
LED_BANDS = {
    "365": 16,  # UV
    "400": 19,
    "460": 20,
    "520": 21,
    "590": 22,
    "660": 23,
    "730": 24,
    "850": 25,  # IR
}

# cor aproximada de cada banda (so pra UI)
LED_COLORS = {
    "365": "#7a3cff", "400": "#5b2bd6", "460": "#2b6bff", "520": "#22c55e",
    "590": "#f2c200", "660": "#ef4444", "730": "#b91c1c", "850": "#6b1a1a",
}

# LED_PWM = True  -> brilho por PWM (chaveia; permite dimmer, mas gera crosstalk)
# LED_PWM = False -> liga/desliga puro (sem chaveamento; evita o crosstalk)
# PWM por software p/ controle de brilho (com os pull-downs no DIM, o PWM se
# comporta bem). False = liga/desliga puro. get_all_bands captura sempre em
# brilho cheio (CAPTURE_DC), independente disso.
LED_PWM = True

PWM_FREQ = 1000  # Hz do PWM software dos LEDs (alto o bastante p/ nao piscar)
CAPTURE_DC = 255  # brilho usado nas capturas (get_all_bands)

# ── Calibracao de reflectancia (100%) ──────────────────────────────────
# Captura uma foto por banda; o usuario desenha uma bounding box sobre a foto
# da banda REFL_BASE_BAND (mesma regiao vale pra todas -- camera fixa, so o
# LED muda entre os frames). O resultado (media por banda dentro da bbox) so
# vai pro banco depois que o usuario confirma a selecao (param_reflectance_compute).
REFL_BASE_BAND = "660"  # banda (visivel) mostrada na UI p/ desenhar a bbox
_reflectance_capture = {}          # {"session": str, "frames": {nm: ndarray}}
_reflectance_lock = threading.Lock()

# ultimo brilho "ligado" de cada banda, pra o toggle restaurar
_led_last = {nm: 255 for nm in LED_BANDS}


def _led_set(pi, pin, dc):
    """Aplica dc (0-255) numa banda. Em modo nao-PWM, qualquer dc>0 = full on."""
    if LED_PWM:
        pi.set_PWM_dutycycle(pin, dc)
    else:
        pi.write(pin, 1 if dc > 0 else 0)


# ── Auto-desligamento das bandas NO SERVIDOR ──────────────────────────
# O contador visual nas telas e so feedback; quem GARANTE o desligamento e
# este timer aqui, porque ele roda no processo do Django, independente da
# aba do navegador estar aberta, em foco, ou o notebook ter dormido no meio
# do caminho. Mantenha LED_AUTO_OFF_SECONDS igual ao COUNTDOWN_START do JS
# (tests.html e parameterization.html) pra tela e servidor baterem.
LED_AUTO_OFF_SECONDS = 3
_led_off_timers = {}              # nm -> threading.Timer pendente
_led_timer_lock = threading.Lock()  # protege o dict acima


def _cancel_led_off(nm):
    with _led_timer_lock:
        t = _led_off_timers.pop(nm, None)
    if t is not None:
        t.cancel()


def _auto_off_led(nm, pin):
    """Alvo do threading.Timer -- roda numa thread propria do processo do
    Django, nao depende de nenhum navegador estar aberto."""
    with _led_timer_lock:
        _led_off_timers.pop(nm, None)
    pi = get_pi()
    _led_set(pi, pin, 0)
    print(f"{nm}nm -> desligado automaticamente (timeout de {LED_AUTO_OFF_SECONDS}s)")


def _schedule_led_off(nm, pin):
    """(Re)agenda o desligamento automatico dessa banda daqui a
    LED_AUTO_OFF_SECONDS. Chamar de novo antes disso reinicia a contagem."""
    _cancel_led_off(nm)
    t = threading.Timer(LED_AUTO_OFF_SECONDS, _auto_off_led, args=(nm, pin))
    t.daemon = True
    with _led_timer_lock:
        _led_off_timers[nm] = t
    t.start()

# ─────────────────────────────────────────────────────────────
# Motor de passo (DRV8825) — portado do motor_web_raspberry.py p/ pigpio
# ─────────────────────────────────────────────────────────────
STEP_PIN = 13
DIR_PIN = 6
EN_PIN = 5     # LOW = habilitado | HIGH = bobinas desligadas
LIMIT_PIN = 26  # fim de curso, ativo em LOW (pull-up)

STEPS_PER_REV = 200
DEGREES_PER_MOVE = 18
STEP_HALF_US = 1200  # meia-largura do pulso STEP em microssegundos (via wave)

# Homing (botao "Posicionar"): vai ATE o fim de curso no sentido horario, em
# velocidade NORMAL (wave/timing de hardware, sem travadinha), checando o switch
# durante o movimento e parando na hora que aciona.
HOME_DIR_HORARIO = True   # sentido que leva ao fim de curso (validado)
HOME_HALF_US = 2000       # meia-largura do passo no homing (us) -> ~250 passos/s: suave
                          # (wave, sem travadinha) e mais devagar que os filtros p/ margem
HOME_BATCH = 20           # passos por lote de wave (checa o switch durante o lote)
MAX_HOME_STEPS = 4000     # trava de seguranca: aborta se nao achar o switch nesse limite

_motor_pos = 0.0
_motor_lock = threading.Lock()

_gpio_ready = False


def _ensure_gpio(pi):
    """Configura pinos de LED (PWM) e do motor uma unica vez."""
    global _gpio_ready
    if _gpio_ready or not pi.connected:
        return
    # LEDs: prepara PWM (se habilitado) ou saida simples, apagados
    for pin in LED_BANDS.values():
        if LED_PWM:
            pi.set_PWM_frequency(pin, PWM_FREQ)
            pi.set_PWM_range(pin, 255)
        else:
            pi.set_mode(pin, pigpio.OUTPUT)
            pi.write(pin, 0)
    # Motor: EN comeca em HIGH (desabilitado), STEP/DIR em LOW
    pi.set_mode(STEP_PIN, pigpio.OUTPUT)
    pi.set_mode(DIR_PIN, pigpio.OUTPUT)
    pi.set_mode(EN_PIN, pigpio.OUTPUT)
    pi.set_mode(LIMIT_PIN, pigpio.INPUT)
    pi.set_pull_up_down(LIMIT_PIN, pigpio.PUD_UP)
    pi.write(STEP_PIN, 0)
    pi.write(DIR_PIN, 0)
    pi.write(EN_PIN, 1)
    _gpio_ready = True


def _limit_triggered(pi):
    return pi.read(LIMIT_PIN) == 0  # ativo em LOW


def _pos_steps():
    """Posicao atual em passos (a partir do fim de curso)."""
    return round(_motor_pos / 360.0 * STEPS_PER_REV)


def _girar_nolock(pi, horario, passos):
    """Move `passos` via wave (timing de hardware). SEM lock (uso interno)."""
    global _motor_pos
    if passos <= 0:
        return
    pi.write(DIR_PIN, 0 if horario else 1)  # DIR invertido p/ bater com os rotulos
    pi.write(EN_PIN, 0)   # habilita
    time.sleep(0.002)     # settle do enable antes de pulsar
    sent = 0
    try:
        pi.wave_clear()
        pulses = []
        for _ in range(passos):
            pulses.append(pigpio.pulse(1 << STEP_PIN, 0, STEP_HALF_US))  # STEP alto
            pulses.append(pigpio.pulse(0, 1 << STEP_PIN, STEP_HALF_US))  # STEP baixo
        pi.wave_add_generic(pulses)
        wid = pi.wave_create()
        pi.wave_send_once(wid)
        while pi.wave_tx_busy():
            time.sleep(0.001)
        pi.wave_delete(wid)
        sent = passos
    finally:
        pi.write(EN_PIN, 1)   # desabilita sempre
        delta = sent / STEPS_PER_REV * 360.0
        _motor_pos += delta if horario else -delta


def girar(pi, horario, passos):
    """Gira o motor (com lock). EN sempre volta pra HIGH no fim."""
    with _motor_lock:
        _girar_nolock(pi, horario, passos)


def _home_nolock(pi):
    """Vai ate o fim de curso no sentido HOME_DIR_HORARIO em velocidade normal
    (waves), checando o switch durante o movimento e parando na hora. Zera a
    posicao. Retorna True se achou. SEM lock."""
    global _motor_pos
    pi.write(DIR_PIN, 0 if HOME_DIR_HORARIO else 1)
    pi.write(EN_PIN, 0)
    time.sleep(0.002)
    found = _limit_triggered(pi)
    n = 0
    try:
        while not found and n < MAX_HOME_STEPS:
            pi.wave_clear()
            pulses = []
            for _ in range(HOME_BATCH):
                pulses.append(pigpio.pulse(1 << STEP_PIN, 0, HOME_HALF_US))
                pulses.append(pigpio.pulse(0, 1 << STEP_PIN, HOME_HALF_US))
            pi.wave_add_generic(pulses)
            wid = pi.wave_create()
            pi.wave_send_once(wid)
            while pi.wave_tx_busy():
                if _limit_triggered(pi):
                    pi.wave_tx_stop()      # para na hora que o switch aciona
                    found = True
                    break
                time.sleep(0.0005)
            pi.wave_delete(wid)
            n += HOME_BATCH
    finally:
        pi.write(EN_PIN, 1)  # desabilita sempre
    if found:
        _motor_pos = 0.0     # referencia
    return found


# ─────────────────────────────────────────────────────────────
# Views basicas
# ─────────────────────────────────────────────────────────────
@login_required
def index(request):
    return render(request, "pages/index.html", {})


@login_required
def video_feed(request):
    global _active_stream
    # Protege tambem contra uma reconexao automatica do browser sem Stop.
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
                             "error": "O stream anterior ainda está encerrando."}, status=503)
    state = {"stop": threading.Event(), "finished": threading.Event()}
    with _stream_state_lock:
        _active_stream = state
    return StreamingHttpResponse(
        _controlled_stream(state),
        content_type='multipart/x-mixed-replace; boundary=frame')


@login_required
def video_feed_stop(request):
    """Encerra explicitamente o MJPEG e so responde apos liberar a camera."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    stopped = _stop_active_stream()
    if not stopped:
        return JsonResponse({"ok": False,
                             "error": "A câmera não encerrou o stream a tempo."}, status=503)
    return JsonResponse({"ok": True})


@csrf_exempt
def img_segmentation(request):
    if request.method == "POST":
        data_url = request.POST.get("imagem")
        fmt, imgstr = data_url.split(';base64,')
        ext = fmt.split('/')[-1]
        data = ContentFile(base64.b64decode(imgstr), name='sample_selected.' + ext)
        from django.conf import settings
        path = os.path.join(settings.MEDIA_ROOT, data.name)
        with open(path, 'wb') as f:
            f.write(data.read())
        return JsonResponse({"status": "ok", "url": f"/media/{data.name}"})
    return JsonResponse({"status": "erro"})


# ─────────────────────────────────────────────────────────────
# Pagina de tests (painel unico)
# ─────────────────────────────────────────────────────────────
@login_required
def tests(request):
    bands = [{"nm": nm, "pin": pin, "color": LED_COLORS[nm]}
             for nm, pin in LED_BANDS.items()]
    return render(request, "pages/tests.html", {"bands": bands})


# ─────────────────────────────────────────────────────────────
# API JSON usada pelo painel (fetch)
# ─────────────────────────────────────────────────────────────
def _led_dc(pi, pin):
    try:
        if LED_PWM:
            return int(pi.get_PWM_dutycycle(pin))
        return 255 if pi.read(pin) else 0
    except Exception:
        return 0


@login_required
def tests_status(request):
    """Estado atual de tudo, pro polling do painel."""
    pi = get_pi()
    leds = {nm: _led_dc(pi, pin) for nm, pin in LED_BANDS.items()}
    return JsonResponse({
        "leds": leds,
        "motor": {"pos": round(_motor_pos, 1), "steps": _pos_steps(),
                  "limit": _limit_triggered(pi)},
    })


@login_required
def tests_led(request):
    """Controla uma banda. Params: band, e (action=toggle | brightness=0-255)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    nm = request.POST.get('band')
    pin = LED_BANDS.get(nm)
    if pin is None:
        return JsonResponse({"error": "banda invalida"}, status=400)

    pi = get_pi()
    if 'brightness' in request.POST:
        dc = max(0, min(255, int(request.POST.get('brightness'))))
        _led_set(pi, pin, dc)
        if dc > 0:
            _led_last[nm] = dc
    else:  # toggle
        if _led_dc(pi, pin) > 0:
            _led_set(pi, pin, 0)
        else:
            _led_set(pi, pin, _led_last.get(nm, 255))

    dc_now = _led_dc(pi, pin)
    if dc_now > 0:
        _schedule_led_off(nm, pin)  # (re)agenda o auto-off no servidor
    else:
        _cancel_led_off(nm)         # ja apagada -> sem timer pendente

    print(f"{nm}nm -> dc={dc_now}")
    return JsonResponse({"band": nm, "dc": dc_now})


@login_required
def tests_leds_off(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    pi = get_pi()
    for nm, pin in LED_BANDS.items():
        _led_set(pi, pin, 0)
        _cancel_led_off(nm)
    print("Turn off all leds!")
    return JsonResponse({"ok": True})


@login_required
def tests_motor(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    horario = (request.POST.get('dir') == 'cw')
    passos = round(DEGREES_PER_MOVE * STEPS_PER_REV / 360.0)
    pi = get_pi()
    girar(pi, horario, passos)
    return JsonResponse({"pos": round(_motor_pos, 1), "limit": _limit_triggered(pi)})


@login_required
def tests_motor_reset(request):
    global _motor_pos
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    _motor_pos = 0.0
    return JsonResponse({"pos": 0.0})


@login_required
def tests_capture(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    action = request.POST.get('action')
    pi = get_pi()

    if action == 'get_frame':
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        camera.save_frame(filename=f"capture_{now}.png")
        return JsonResponse({"ok": True, "mode": "frame"})

    if action == 'get_all_bands':
        # apaga tudo, captura banda por banda em brilho cheio, apaga de novo
        for nm in LED_BANDS:
            _cancel_led_off(nm)  # evita apagar a banda no meio da exposicao
        for pin in LED_BANDS.values():
            _led_set(pi, pin, 0)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        for nm, pin in LED_BANDS.items():
            _led_set(pi, pin, CAPTURE_DC)
            time.sleep(0.2)  # estabiliza LED/exposicao
            camera.save_frame(filename=f"{nm}nm_{now}.png", folder=f"captures_{now}")
            _led_set(pi, pin, 0)
        return JsonResponse({"ok": True, "mode": "all_bands", "folder": f"captures_{now}"})

    return JsonResponse({"error": "acao invalida"}, status=400)


# ─────────────────────────────────────────────────────────────
# Parametrizacao (filtros + intensidade das bandas) — salva no banco
# ─────────────────────────────────────────────────────────────
def _ensure_param_rows():
    """Garante 6 filtros e as 8 bandas no banco (idempotente)."""
    for i in range(1, 7):
        FilterPosition.objects.get_or_create(index=i)
    for order, nm in enumerate(LED_BANDS):
        BandParameter.objects.get_or_create(nm=nm, defaults={"order": order})


@login_required
def parameterization(request):
    _ensure_param_rows()
    filters = list(FilterPosition.objects.all())
    bparams = {b.nm: b.intensity for b in BandParameter.objects.all()}
    bands = [{"nm": nm, "color": LED_COLORS[nm], "intensity": bparams.get(nm, 0)}
             for nm in LED_BANDS]
    cal = GeometricCalibration.objects.first()   # calibracao mais recente
    refl = ReflectanceCalibration.objects.first()   # calibracao de reflectancia 100% mais recente
    return render(request, "pages/parameterization.html",
                  {"filters": filters, "bands": bands, "cal": cal, "refl": refl})


@login_required
def param_motor(request):
    """Jog do motor por um numero de passos (para posicionar filtros)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    horario = (request.POST.get('dir') == 'cw')
    steps = max(1, min(int(request.POST.get('steps', 10)), 2000))
    pi = get_pi()
    girar(pi, horario, steps)
    return JsonResponse({"pos": round(_motor_pos, 1), "steps": _pos_steps(),
                         "limit": _limit_triggered(pi)})


@login_required
def param_save(request):
    """Salva nome/steps dos 6 filtros e a intensidade das 8 bandas."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    data = json.loads(request.body or "{}")

    for f in data.get('filters', []):
        try:
            idx = int(f['index'])
        except (KeyError, ValueError, TypeError):
            continue
        name = (f.get('name') or "").strip() or DEFAULT_FILTER_NAME
        try:
            steps = int(f.get('steps') or 0)
        except (ValueError, TypeError):
            steps = 0
        FilterPosition.objects.filter(index=idx).update(name=name, steps=steps)

    for b in data.get('bands', []):
        nm = b.get('nm')
        try:
            intensity = max(0, min(255, int(b.get('intensity') or 0)))
        except (ValueError, TypeError):
            intensity = 0
        BandParameter.objects.filter(nm=nm).update(intensity=intensity)

    return JsonResponse({"ok": True})


@login_required
def param_posicionar(request):
    """Vai ao fim de curso (referencia) e depois anda `steps` no sentido inverso
    ao homing, chegando na posicao do filtro."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    steps = min(abs(int(request.POST.get('steps', 0))), MAX_HOME_STEPS)
    pi = get_pi()
    with _motor_lock:
        found = _home_nolock(pi)
        if not found:
            return JsonResponse({"ok": False,
                                 "error": "Fim de curso nao encontrado (verifique o sentido do homing).",
                                 "pos": round(_motor_pos, 1), "steps": _pos_steps(),
                                 "limit": _limit_triggered(pi)}, status=409)
        if steps > 0:
            _girar_nolock(pi, not HOME_DIR_HORARIO, steps)  # sentido inverso ao homing
    return JsonResponse({"ok": True, "pos": round(_motor_pos, 1),
                         "steps": _pos_steps(), "limit": _limit_triggered(pi)})


def _qr_mm_from_content(text):
    """Extrai o tamanho em mm do conteudo do QR (primeiro numero).
    Ex.: '50', '50x50', '50mm' -> 50.0. None se nao achar numero."""
    m = re.search(r'\d+(?:[.,]\d+)?', text or "")
    return float(m.group().replace(',', '.')) if m else None


@login_required
def param_geo_frame(request):
    """Captura um frame, detecta o QR (tamanho no conteudo) e calcula mm/pixel."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)

    frame = camera.grab()
    if frame is None:
        return JsonResponse({"ok": False, "error": "Sem imagem da câmera."}, status=409)

    data, points, _ = cv2.QRCodeDetector().detectAndDecode(frame)
    if points is None:
        return JsonResponse({"ok": False,
                             "error": "Nenhum QR detectado no frame."}, status=422)

    qr_mm = _qr_mm_from_content(data)
    if qr_mm is None or qr_mm <= 0:
        return JsonResponse({"ok": False,
                             "error": f"QR detectado, mas sem tamanho no conteúdo "
                                      f"('{data}'). Codifique o lado em mm (ex.: '50')."},
                            status=422)

    pts = np.array(points, dtype=float).reshape(-1, 2)   # 4 cantos
    sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
    avg_px = sum(sides) / len(sides)
    deviation = (max(sides) - min(sides)) / avg_px if avg_px else 1.0
    mm_per_pixel = qr_mm / avg_px
    px_per_mm = avg_px / qr_mm

    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ok_enc, buf = cv2.imencode('.png', frame)   # frame -> PNG em memoria

    cal = GeometricCalibration(
        mm_per_pixel=mm_per_pixel, px_per_mm=px_per_mm, qr_mm=qr_mm, qr_px=avg_px,
        qr_content=(data or "")[:200], deviation=deviation)
    if ok_enc:
        cal.image.save(f"geo_{now}.png", ContentFile(buf.tobytes()), save=False)
    cal.save()

    return JsonResponse({
        "ok": True,
        "mm_per_pixel": round(mm_per_pixel, 6),
        "px_per_mm": round(px_per_mm, 4),
        "qr_mm": qr_mm,
        "qr_px": round(avg_px, 1),
        "deviation_pct": round(deviation * 100, 1),
        "content": data,
        "tilted": deviation > 0.05,   # >5% de divergencia entre os lados = torto
        "image_url": cal.image.url if cal.image else "",
    })


@login_required
def param_reflectance_capture(request):
    """Tira uma foto por banda (LED_BANDS, brilho cheio) e guarda os frames em
    memoria (processo do Django). Retorna a foto da banda REFL_BASE_BAND (como
    data URL) para o usuario desenhar a bounding box da referencia 100%.

    Tambem salva uma copia de cada banda em media/reflectance/refl_<timestamp>/,
    so pra conferirmos as fotos tiradas (modo de teste)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    # Encerra qualquer stream MJPEG ativo: a camera UVC so aceita um leitor
    # por vez e, com o stream aberto, a captura pode travar ou entregar frames
    # invalidos. O botao na pagina ja para o stream; aqui e so a rede de seguranca.
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    pi = get_pi()
    for nm in LED_BANDS:
        _cancel_led_off(nm)  # evita apagar a banda no meio da exposicao
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    frames = {}
    base_frame = None
    try:
        for nm, pin in LED_BANDS.items():
            _led_set(pi, pin, CAPTURE_DC)
            time.sleep(3)  # da tempo do LED subir e da exposicao estabilizar
            frame = camera.grab()
            _led_set(pi, pin, 0)
            time.sleep(1.5)  # espera o LED apagar antes da proxima banda
            if frame is None:
                return JsonResponse({"ok": False,
                    "error": f"Sem imagem da câmera (banda {nm}nm)."}, status=409)
            frames[nm] = frame
            if nm == REFL_BASE_BAND:
                base_frame = frame
    finally:
        for pin in LED_BANDS.values():
            _led_set(pi, pin, 0)

    # copia de teste: 1 PNG por banda, em media/reflectance/refl_<timestamp>/
    folder = f"refl_{stamp}"
    out_dir = os.path.join(settings.MEDIA_ROOT, "reflectance", folder)
    os.makedirs(out_dir, exist_ok=True)
    for nm, frame in frames.items():
        cv2.imwrite(os.path.join(out_dir, f"{nm}nm.png"), frame)
    print(f"Captura de reflectancia salva em: {out_dir}")

    ok_enc, buf = cv2.imencode('.png', base_frame)
    if not ok_enc:
        return JsonResponse({"ok": False, "error": "Falha ao codificar imagem."}, status=500)

    session = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with _reflectance_lock:
        _reflectance_capture.clear()
        _reflectance_capture['session'] = session
        _reflectance_capture['frames'] = frames

    b64 = base64.b64encode(buf.tobytes()).decode('ascii')
    return JsonResponse({"ok": True, "session": session, "folder": folder,
                         "image_data": f"data:image/png;base64,{b64}",
                         "width": int(base_frame.shape[1]), "height": int(base_frame.shape[0])})


@login_required
def param_reflectance_compute(request):
    """Recebe a bbox (pixels, na imagem da banda REFL_BASE_BAND) escolhida pelo
    usuario; calcula a media de cada banda dentro dela e salva a calibracao."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    data = json.loads(request.body or "{}")

    with _reflectance_lock:
        session = _reflectance_capture.get('session')
        frames = _reflectance_capture.get('frames')

    if not frames or data.get('session') != session:
        return JsonResponse({"ok": False,
            "error": "Sessão de captura expirada — tire as fotos de novo."}, status=409)

    try:
        x = max(0, int(data['x'])); y = max(0, int(data['y']))
        w = max(1, int(data['w'])); h = max(1, int(data['h']))
    except (KeyError, ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Bounding box inválida."}, status=400)

    means = {}
    for nm, frame in frames.items():
        fh, fw = frame.shape[:2]
        x0, y0 = min(x, fw - 1), min(y, fh - 1)
        x1, y1 = min(x + w, fw), min(y + h, fh)
        crop = frame[y0:y1, x0:x1]
        means[nm] = round(float(crop.mean()), 2) if crop.size else 0.0

    cal = ReflectanceCalibration(means=means, bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h)
    base_frame = frames.get(REFL_BASE_BAND)
    if base_frame is not None:
        ok_enc, buf = cv2.imencode('.png', base_frame)
        if ok_enc:
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            cal.image.save(f"refl_{now}.png", ContentFile(buf.tobytes()), save=False)
    cal.save()

    # Mantem a sessao/frames vivos: o usuario pode arrastar de novo pra ajustar
    # a bbox e recalcular quantas vezes quiser sobre as MESMAS fotos. So e
    # substituida quando uma nova captura acontecer (param_reflectance_capture).

    return JsonResponse({"ok": True, "means": means})
