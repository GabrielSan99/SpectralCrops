from functools import wraps

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse, JsonResponse, FileResponse
from django.core.files.base import ContentFile
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

import pigpio
from .camera_functions import ArducamCamera
from .models import (FilterPosition, BandParameter, GeometricCalibration,
                     ReflectanceCalibration, ReflectanceZeroCalibration,
                     DataAcquisition, ROIMeasurement, Project, Annotation, CameraSettings,
                     DEFAULT_FILTER_NAME)
import os
import shutil
import tempfile
import zipfile
import csv
import io
from datetime import datetime
import base64
import json
import re
import threading
import time
import cv2
import numpy as np


# ─────────────────────────────────────────────────────────────
# Projeto ativo (sessao do navegador) -- Data Acquisition, Parametrizacao e
# Analysis só ficam acessíveis com um projeto selecionado na Home; as
# calibracoes e aquisicoes feitas a partir dai ficam vinculadas a ele.
# ─────────────────────────────────────────────────────────────
def _get_active_project(request):
    pid = request.session.get('active_project_id')
    if not pid:
        return None
    return Project.objects.filter(id=pid).first()


def require_project(view_func):
    """Gate de pagina: sem projeto ativo, manda pra Home com um aviso."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not _get_active_project(request):
            messages.warning(request, "Selecione um projeto na Home antes de continuar.")
            return redirect('index')
        return view_func(request, *args, **kwargs)
    return wrapper


def _band_camera_last_changed(project):
    """Timestamp da ultima mudanca em CameraSettings ou em QUALQUER
    BandParameter (intensidade/exposicao) desse projeto -- None se nao
    existir nenhum dos dois ainda. Usado pra saber se uma calibracao de
    reflectancia ficou desatualizada (ver _reflectance_stale): ela so vale
    pras condicoes de captura (camera + LED) do momento em que foi feita."""
    ts = None
    cs = CameraSettings.objects.filter(project=project).first()
    if cs:
        ts = cs.updated
    band_ts = BandParameter.objects.filter(project=project).aggregate(m=Max('updated'))['m']
    if band_ts and (ts is None or band_ts > ts):
        ts = band_ts
    return ts


def _reflectance_stale(project):
    """(refl_stale, refl0_stale): True quando a calibracao de reflectancia
    100%/0% (respectivamente) EXISTE mas e mais antiga que a ultima mudanca
    de camera/iluminacao -- ou seja, foi feita sob condicoes que ja nao
    valem mais e precisa ser refeita antes de confiar nos dados. Quando a
    calibracao nem existe ainda, isso e "faltando" (ver _missing_calibrations),
    nao "desatualizada" -- aqui sempre False nesse caso."""
    changed = _band_camera_last_changed(project)
    if not changed:
        return False, False
    refl = ReflectanceCalibration.objects.filter(project=project).first()
    refl0 = ReflectanceZeroCalibration.objects.filter(project=project).first()
    refl_stale = bool(refl and refl.created < changed)
    refl0_stale = bool(refl0 and refl0.created < changed)
    return refl_stale, refl0_stale


def _calibration_status(project):
    """Status das 3 calibracoes desse projeto (espacial, reflectancia 100% e
    0%), cada uma em um de 3 estados -- "missing" (nunca feita), "stale"
    (feita, mas camera/LED mudaram depois -- so se aplica as 2 de
    reflectancia, ver _reflectance_stale) ou "ok". Usado tanto pro gate de
    Data Acquisition (_missing_calibrations) quanto pros chips de status na
    Home (ver views.index)."""
    geo_ok = GeometricCalibration.objects.filter(project=project).exists()
    refl_exists = ReflectanceCalibration.objects.filter(project=project).exists()
    refl0_exists = ReflectanceZeroCalibration.objects.filter(project=project).exists()
    refl_stale, refl0_stale = _reflectance_stale(project) if (refl_exists or refl0_exists) else (False, False)

    def _status(exists, stale):
        if not exists:
            return "missing"
        return "stale" if stale else "ok"

    return {"geo": "ok" if geo_ok else "missing",
            "refl": _status(refl_exists, refl_stale),
            "refl0": _status(refl0_exists, refl0_stale)}


def _missing_calibrations(project, status=None):
    """Lista (em PT) o que falta calibrar (ou recalibrar) nesse projeto pra
    Data Acquisition fazer sentido -- sem isso, a captura nao tem % de
    reflectancia pra basear nada. Reflectancia desatualizada (parametros de
    camera/LED mudaram depois da calibracao, ver _reflectance_stale) conta
    como faltando tambem -- os dados novos nao seriam comparaveis aos da
    calibracao antiga. `status` opcional evita recalcular _calibration_status
    quando o chamador ja tiver ele em maos (ver views.index).

    A calibracao ESPACIAL (mm/px) NAO entra aqui de proposito -- ainda nao e
    usada em nenhuma conta de verdade (ROIMeasurement guarda tudo em pixels,
    nao em mm), so fica disponivel pra quando isso for implementado. Pode ser
    feita a qualquer momento, inclusive depois de ja existirem aquisicoes --
    ela nao "revela" nada sobre fotos passadas, so passa a valer daqui pra
    frente pra converter pixel -> mm nas medidas (ver GeometricCalibration)."""
    status = status or _calibration_status(project)
    missing = []
    if status["refl"] == "missing":
        missing.append("reflectância 100%")
    elif status["refl"] == "stale":
        missing.append("reflectância 100% (desatualizada — câmera/iluminação mudaram)")
    if status["refl0"] == "missing":
        missing.append("reflectância 0%")
    elif status["refl0"] == "stale":
        missing.append("reflectância 0% (desatualizada — câmera/iluminação mudaram)")
    return missing


def require_calibrations(view_func):
    """Gate de pagina: so libera Data Acquisition se o projeto ativo ja tiver
    as 3 calibracoes feitas (espacial + reflectancia 100%/0%). Chamar DEPOIS
    de @require_project (assume que ja existe projeto ativo)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        project = _get_active_project(request)
        if project:
            missing = _missing_calibrations(project)
            if missing:
                messages.warning(request,
                    "Complete a calibração antes de adquirir dados — falta: " + ", ".join(missing) + ".")
                return redirect('parameterization')
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# Nomeacao padrao de tudo que e salvo em projects/ (aquisicoes e capturas de
# parametrizacao): <timestamp>-<projeto>-<rotulo>. Pras capturas de
# parametrizacao (calibracao espacial/reflectancia) o rotulo e fixo por tipo
# -- o nome so muda pelo timestamp entre uma captura e outra do mesmo projeto.
# Pras aquisicoes, o rotulo e o nome da amostra dado pelo usuario.
# ─────────────────────────────────────────────────────────────
def _slugify(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "sem-nome"


def _dated_name(project, label):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}-{_slugify(project.name)}-{_slugify(label)}"

camera = ArducamCamera()

# ─────────────────────────────────────────────────────────────
# Controles V4L2/UVC da camera (Arducam OV9281) -- ajustaveis na tela de
# Parametrizacao (CameraSettings, models.py). Faixas conferidas direto na
# camera com `v4l2-ctl -d /dev/video0 --list-ctrls-menus`.
# ─────────────────────────────────────────────────────────────
CAMERA_CONTROL_RANGES = {
    "brightness": (-64, 64), "contrast": (0, 64), "saturation": (0, 128),
    "hue": (-40, 40), "gamma": (72, 500), "gain": (0, 100),
    "sharpness": (0, 6), "backlight_compensation": (0, 2),
    "power_line_frequency": (0, 2),
    "white_balance_temperature": (2800, 6500),
    "exposure_time_absolute": (1, 5000),
}
CAMERA_INT_FIELDS = list(CAMERA_CONTROL_RANGES.keys())
CAMERA_BOOL_FIELDS = ["white_balance_automatic", "auto_exposure", "exposure_dynamic_framerate"]
# Automatico (WB/exposicao) desligado por padrao -- ligado, a camera reajusta
# cor/exposicao sozinha a cada captura, variando o brilho entre bandas
# diferentes e atrapalhando a comparacao de reflectancia entre elas.
CAMERA_DEFAULTS = {
    "brightness": 0, "contrast": 32, "saturation": 64, "hue": 0, "gamma": 100,
    "gain": 0, "sharpness": 3, "backlight_compensation": 1, "power_line_frequency": 2,
    "white_balance_automatic": False, "white_balance_temperature": 4600,
    "auto_exposure": False, "exposure_time_absolute": 157, "exposure_dynamic_framerate": False,
}


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _ensure_camera_settings(project):
    cs, _ = CameraSettings.objects.get_or_create(project=project)
    return cs


def _camera_settings_json(cs):
    """Serializa pro JS -- mesmos nomes de campo do model."""
    return {field: getattr(cs, field) for field in (CAMERA_INT_FIELDS + CAMERA_BOOL_FIELDS)}


def _camera_settings_v4l2_dict(cs):
    """Monta o dict pronto pra ArducamCamera.apply_controls. white_balance_automatic
    e auto_exposure vem primeiro (dict preserva ordem) -- os valores manuais
    dependentes so entram quando o automatico correspondente esta desligado,
    senao o driver rejeita (controle fica "inactive" com o auto ligado).
    NAO inclui exposure_time_absolute -- esse controle e so por banda agora
    (ver _apply_band_exposure, chamado quando um LED acende); incluir aqui
    faria qualquer save de OUTRO controle (brilho, ganho etc.) sobrescrever
    de volta pro valor antigo a exposicao que estava valendo pra banda acesa
    na stream."""
    values = {
        "white_balance_automatic": cs.white_balance_automatic,
        "auto_exposure": 3 if cs.auto_exposure else 1,   # 3=Aperture Priority, 1=Manual
        "brightness": cs.brightness, "contrast": cs.contrast, "saturation": cs.saturation,
        "hue": cs.hue, "gamma": cs.gamma, "gain": cs.gain, "sharpness": cs.sharpness,
        "backlight_compensation": cs.backlight_compensation,
        "power_line_frequency": cs.power_line_frequency,
        "exposure_dynamic_framerate": cs.exposure_dynamic_framerate,
    }
    if not cs.white_balance_automatic:
        values["white_balance_temperature"] = cs.white_balance_temperature
    return values


def _apply_band_exposure(project, nm):
    """Aplica na camera de verdade (stream ao vivo incluida, se estiver
    ligada) o tempo de exposicao manual configurado pra essa banda --
    chamado sempre que um LED acende no preview (toggle ou slider, ver
    tests_led), pra a imagem ao vivo refletir a MESMA exposicao que vai
    valer quando essa banda for capturada de verdade (calibracao/aquisicao,
    ver _capture_exposure). So faz sentido com a exposicao automatica
    desligada -- ligada, a camera decide sozinha e forcar um valor manual
    aqui so seria sobrescrito de novo no proximo frame (CameraSettings.
    auto_exposure, toggle no card Iluminacao)."""
    if not project:
        return
    cam = CameraSettings.objects.filter(project=project).first()
    if cam and cam.auto_exposure:
        return
    bp = BandParameter.objects.filter(project=project, nm=nm).first()
    exposure = bp.exposure_time_absolute if bp else 157
    camera.apply_controls({"auto_exposure": 1, "exposure_time_absolute": exposure})


def _apply_camera_settings(project):
    """Envia os controles salvos desse projeto pra camera de verdade. Best
    effort: se a camera nao estiver conectada, so loga -- nunca derruba a
    pagina por causa disso."""
    if not project:
        return True, ""
    cs = _ensure_camera_settings(project)
    ok, err = camera.apply_controls(_camera_settings_v4l2_dict(cs))
    if not ok:
        print(f"Falha ao aplicar controles da câmera: {err}")
    return ok, err


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

# Tempo parado com o LED aceso antes de tirar a foto (banda por banda), pra
# dar tempo do LED subir ao brilho maximo e a exposicao/auto-exposure da
# camera estabilizar -- usado tanto na aquisicao de dados quanto na
# calibracao de reflectancia 100% (fotos tem que ser comparaveis).
# capture_stabilized le frames de verdade durante essa espera (camera aberta
# o tempo todo), entao o valor aqui converge de fato -- ajustado pra 5s de
# novo (usuario pediu, 3s nao estava sendo suficiente numa avaliacao mais recente).
LED_STABILIZE_SECONDS = 3

# Tempo de espera pra deixar a exposicao AUTOMATICA convergir de verdade
# antes de usar o frame -- maior que LED_STABILIZE_SECONDS de proposito: o
# algoritmo de auto-exposure sobe aos poucos (nao pula direto pro valor
# final), e ler cedo demais pega ele no meio do caminho, nao convergido (foi
# o que fazia toda banda "descobrir" um valor proximo do teto/5000 em
# param_band_auto_expose mesmo em bandas que deveriam precisar de bem menos).
# Usado tanto la quanto na captura do QR da calibracao espacial (param_geo_frame,
# que forca automatico pra nao depender de exposicao manual mal ajustada) --
# nenhum dos dois roda a cada captura de verdade, entao o tempo extra nao
# pesa no dia a dia.
BAND_AUTO_EXPOSE_SECONDS = 8


# Banda usada pra iluminar o ambiente na calibracao espacial (achar o QR) --
# a caixa e fechada/escura, sem isso o QR nao aparece exposto o suficiente
# pra ser detectado. Nao afeta a medida de mm/px (so precisa dar luz
# suficiente pro detector enxergar), mas a banda importa na pratica: 660nm
# (vermelho) e uma escolha melhor que 365nm (UV, o padrao antigo) pra
# iluminar um QR IMPRESSO EM PAPEL comum -- LED UV costuma ser bem mais
# fraco opticamente que um vermelho no mesmo drive, e lentes/sensores tem
# sensibilidade normalmente pior perto do UV, exigindo exposicao enorme pra
# um retorno de luz ainda fraco (o que factivelmente pode ter sido a causa
# de nao conseguir ler o QR mesmo com exposicao automatica). 660nm tambem
# ja e a banda de referencia visual usada em outras telas (REFL_BASE_BAND).
# O usuario pode trocar a banda na propria tela de Parametrizacao -- isso
# aqui e so o valor padrao/de fabrica.
GEO_CAL_LED_NM = "660"

# ── Calibracao de reflectancia (100%) ──────────────────────────────────
# Captura uma foto por banda; o usuario desenha uma bounding box sobre a foto
# da banda REFL_BASE_BAND (mesma regiao vale pra todas -- camera fixa, so o
# LED muda entre os frames). O resultado (media por banda dentro da bbox) so
# vai pro banco depois que o usuario confirma a selecao (param_reflectance_compute).
REFL_BASE_BAND = "660"  # banda (visivel) mostrada na UI p/ desenhar a bbox
_reflectance_capture = {}          # {"session": str, "frames": {nm: ndarray}}
_reflectance_lock = threading.Lock()

# Faixa MINIMA (branco - escuro, em niveis de cinza 0-255) pra confiar numa
# banda calibrada. Abaixo disso, o LED daquela banda nao esta entregando luz
# suficiente no alvo branco (visto na pratica: 590/660/730nm com uma faixa
# de 4-12 contra 84-97 nas bandas boas) -- normalizar/calcular reflectancia
# com um denominador desse tamanho amplifica ruido do sensor (~1-2 niveis de
# cinza) em dezenas de vezes, produzindo imagem normalizada em ruido
# colorido/estatico (nada a ver com a cena) e reflectancia em % sem sentido
# (chegou a 1458% num ROI real). Bandas abaixo desse minimo NAO sao
# normalizadas/tem reflectancia calculada -- ficam so com o valor bruto, ate
# a calibracao (ou o hardware por tras dela) melhorar.
MIN_REFLECTANCE_RANGE = 15

# Media bruta (0-255) acima disso conta como ROI SATURADO -- o sensor e de 8
# bits, entao uma media de ROI tao perto do teto so acontece quando quase
# todo pixel ali ja bateu em 255 (nao e "muito claro", e clipado/sem
# informacao real).
SATURATION_RAW_THRESHOLD = 250

# Teto de reflectancia (%) que ainda faz sentido fisico -- um pouco acima de
# 100% cobre ruido normal de medicao, mas nao os casos vistos na pratica
# (150-1450%). Pega tambem o ROI que NAO chegou a clipar no sensor (raw bem
# abaixo de 255) mas ainda assim mediu mais claro que o proprio branco de
# referencia -- normalmente reflexo especular na superficie da semente
# (brilho pontual mais intenso que o alvo branco difuso) ou geometria/
# intensidade de LED fortes demais pra aquele ponto -- em qualquer um dos
# casos nao e reflectancia difusa de verdade, e SATURATION_RAW_THRESHOLD
# sozinho nao pega (visto na pratica: raw=227.7 sem bater no teto, mas ainda
# assim 154% -- muito acima do branco de 100%). Ver _roi_reflectance_pct.
REFLECTANCE_MAX_PCT = 110

# ultimo brilho "ligado" de cada banda, pra o toggle restaurar
_led_last = {}


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

# Seguranca: nunca girar o motor com LED aceso ou a camera com o device aberto
# (vibra a bancada durante uma exposicao/medida). Toda rotina que MOVE o motor
# chama isso primeiro.
MOTOR_SAFETY_DELAY = 3  # segundos parado, com tudo desligado, antes de girar


def _prepare_motor_move(pi):
    """So espera os MOTOR_SAFETY_DELAY segundos se havia algo aceso/ativo pra
    desligar -- jog repetido com tudo ja apagado nao fica travando a toa."""
    was_lit = any(_led_dc(pi, pin) > 0 for pin in LED_BANDS.values())
    was_streaming = _active_stream is not None

    for nm in LED_BANDS:
        _cancel_led_off(nm)
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)
    _stop_active_stream()

    if was_lit or was_streaming:
        time.sleep(MOTOR_SAFETY_DELAY)


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
    projects = Project.objects.all()
    active_project = _get_active_project(request)

    missing_calibrations = []
    calib_status = {"geo": "missing", "refl": "missing", "refl0": "missing"}
    stats = None
    recent_acquisitions = []
    refl_chart = []
    if active_project:
        calib_status = _calibration_status(active_project)
        missing_calibrations = _missing_calibrations(active_project, status=calib_status)

        acqs_qs = DataAcquisition.objects.filter(project=active_project).select_related('annotation')
        total_acq = acqs_qs.count()
        annotated = 0
        for acq in acqs_qs:
            ann = getattr(acq, 'annotation', None)
            if ann and (ann.boxes or ann.polygons or ann.points):
                annotated += 1
        roi_count = ROIMeasurement.objects.filter(project=active_project).count()
        stats = {"total_acq": total_acq, "annotated": annotated, "roi_count": roi_count}

        for acq in acqs_qs.order_by('-created')[:5]:
            recent_acquisitions.append({
                "id": acq.id, "name": acq.name or f"Aquisição {acq.id}",
                "created": acq.created, "thumb_url": _acquisition_ref_image_url(acq),
            })

        refl = ReflectanceCalibration.objects.filter(project=active_project).first()
        if refl:
            refl_chart = [{"nm": nm, "color": LED_COLORS[nm], "value": refl.means[nm]}
                          for nm in LED_BANDS if refl.means.get(nm) is not None]

    return render(request, "pages/index.html", {
        "projects": projects, "active_project": active_project,
        "missing_calibrations": missing_calibrations, "calib_status": calib_status, "stats": stats,
        "recent_acquisitions": recent_acquisitions, "refl_chart": refl_chart,
    })


@login_required
def video_feed(request):
    global _active_stream
    # Protege tambem contra uma reconexao automatica do browser sem Stop.
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
                             "error": "O stream anterior ainda está encerrando."}, status=503)
    _apply_camera_settings(_get_active_project(request))
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
        return JsonResponse({"status": "ok", "url": f"{settings.MEDIA_URL}{data.name}"})
    return JsonResponse({"status": "erro"})


# ─────────────────────────────────────────────────────────────
# Pagina de tests (painel unico)
# ─────────────────────────────────────────────────────────────
@login_required
def tests(request):
    project = _get_active_project(request)
    intensity = {b.nm: b.intensity for b in BandParameter.objects.filter(project=project)} if project else {}
    bands = [{"nm": nm, "pin": pin, "color": LED_COLORS[nm], "intensity": intensity.get(nm, 0)}
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
    """Controla uma banda. Params: band, e (action=toggle | brightness=0-255).
    Sempre que a banda acaba ACESA, aplica na camera (stream ao vivo
    incluida) o tempo de exposicao manual configurado pra ELA -- ver
    _apply_band_exposure."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    nm = request.POST.get('band')
    pin = LED_BANDS.get(nm)
    if pin is None:
        return JsonResponse({"error": "banda invalida"}, status=400)

    project = _get_active_project(request)
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
            # a intensidade CONFIGURADA (BandParameter, tela de Parametrizacao)
            # sempre ganha -- _led_last (ultimo brilho usado via slider nessa
            # mesma execucao do servidor) e so um fallback pra quando nao tem
            # projeto ativo/banda configurada (ex.: tests.html sem projeto
            # selecionado), senao um valor salvo antigo em memoria ficava
            # preso na frente da configuracao de verdade pro resto do processo.
            configured = BandParameter.objects.filter(project=project, nm=nm).values_list(
                'intensity', flat=True).first()
            dc = configured if configured is not None else _led_last.get(nm, 255)
            _led_set(pi, pin, dc)

    dc_now = _led_dc(pi, pin)
    if dc_now > 0:
        _schedule_led_off(nm, pin)  # (re)agenda o auto-off no servidor
        _apply_band_exposure(project, nm)
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
    _prepare_motor_move(pi)
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
# Posicoes de filtro calibradas fisicamente na bancada (passos a partir do
# fim de curso) -- viram o DEFAULT DE FABRICA do software: um projeto novo
# (ou o banco reconstruido do zero) ja nasce com essas posicoes, sem precisar
# rejogar o motor manualmente pra redescobrir onde cada filtro fica. So valem
# na CRIACAO da linha (get_or_create abaixo) -- editar aqui nao muda projeto
# ja existente, so o que for criado dai em diante.
FILTER_DEFAULTS = {
    1: {"name": "Vazio", "steps": -125},
    2: {"name": "Passa Alta 650nm", "steps": -500},
    3: {"name": "Passa Alta 670nm", "steps": -875},
    4: {"name": "Passa Alta 720nm", "steps": -1250},
}

# Intensidade default das 8 bandas de LED (0-255, PWM dutycycle): nasce em
# 100% de brilho, mesma logica do FILTER_DEFAULTS acima -- so vale na CRIACAO
# da linha, editar aqui nao muda projeto ja existente.
BAND_DEFAULT_INTENSITY = 255


def _ensure_param_rows(project):
    """Garante 6 filtros e as 8 bandas no banco PRA ESSE PROJETO (idempotente).
    Filtros 1-4 nascem com FILTER_DEFAULTS; 5-6 (sem default fisico ainda)
    nascem com steps=0. Bandas nascem em BAND_DEFAULT_INTENSITY (100%) --
    projetos clonados de outro nunca passam por aqui vazios, ver
    _clone_param_rows em project_create."""
    for i in range(1, 7):
        FilterPosition.objects.get_or_create(project=project, index=i,
                                             defaults=FILTER_DEFAULTS.get(i, {}))
    for order, nm in enumerate(LED_BANDS):
        BandParameter.objects.get_or_create(project=project, nm=nm,
                                            defaults={"order": order, "intensity": BAND_DEFAULT_INTENSITY})


def _band_capture_params(project):
    """dict nm -> BandParameter (garante as 8 linhas antes). Usado em toda
    captura de verdade (reflectancia 100%/0% e aquisicao) pra cada banda usar
    o brilho de LED e a exposicao manual configurados pra ELA na tela de
    Parametrizacao -- ver BandParameter.exposure_time_absolute."""
    _ensure_param_rows(project)
    return {b.nm: b for b in BandParameter.objects.filter(project=project)}


def _capture_exposure(cam, bp):
    """Exposicao a usar numa captura de verdade: None (automatico) se o
    toggle "Exposicao automatica" (CameraSettings.auto_exposure, card
    Iluminacao) estiver ligado -- nesse caso TODAS as bandas ignoram seu
    BandParameter.exposure_time_absolute e a camera decide sozinha, banda a
    banda. Desligado (padrao), usa o tempo manual configurado pra essa banda."""
    if cam and cam.auto_exposure:
        return None
    return bp.exposure_time_absolute if bp else None


@login_required
@require_project
def parameterization(request):
    project = _get_active_project(request)
    _ensure_param_rows(project)
    filters = list(FilterPosition.objects.filter(project=project))
    bparams = {b.nm: b for b in BandParameter.objects.filter(project=project)}
    bands = [{"nm": nm, "color": LED_COLORS[nm],
              "intensity": bparams[nm].intensity if nm in bparams else 0,
              "exposure_time_absolute": bparams[nm].exposure_time_absolute if nm in bparams else 157}
             for nm in LED_BANDS]
    cal = GeometricCalibration.objects.filter(project=project).first()   # calibracao mais recente DESSE projeto
    refl = ReflectanceCalibration.objects.filter(project=project).first()
    refl0 = ReflectanceZeroCalibration.objects.filter(project=project).first()
    cam = _ensure_camera_settings(project)
    _apply_camera_settings(project)   # garante que o hardware reflita o que esta salvo
    refl_stale, refl0_stale = _reflectance_stale(project)
    return render(request, "pages/parameterization.html",
                  {"filters": filters, "bands": bands, "cal": cal, "refl": refl, "refl0": refl0,
                   "cam": cam, "project": project, "led_stabilize_seconds": LED_STABILIZE_SECONDS,
                   "refl_stale": refl_stale, "refl0_stale": refl0_stale})


@login_required
def param_motor(request):
    """Jog do motor por um numero de passos (para posicionar filtros)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    horario = (request.POST.get('dir') == 'cw')
    steps = max(1, min(int(request.POST.get('steps', 10)), 2000))
    pi = get_pi()
    _prepare_motor_move(pi)
    girar(pi, horario, steps)
    return JsonResponse({"pos": round(_motor_pos, 1), "steps": _pos_steps(),
                         "limit": _limit_triggered(pi)})


@login_required
def param_save(request):
    """Salva nome/steps dos 6 filtros e a intensidade das 8 bandas, do
    projeto ativo."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
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
        FilterPosition.objects.filter(project=project, index=idx).update(name=name, steps=steps)

    for b in data.get('bands', []):
        nm = b.get('nm')
        try:
            intensity = max(0, min(255, int(b.get('intensity') or 0)))
        except (ValueError, TypeError):
            intensity = 0
        updates = {"intensity": intensity, "updated": timezone.now()}
        if 'exposure_time_absolute' in b:
            try:
                updates["exposure_time_absolute"] = max(1, min(5000, int(b.get('exposure_time_absolute') or 157)))
            except (ValueError, TypeError):
                pass
        BandParameter.objects.filter(project=project, nm=nm).update(**updates)

    return JsonResponse({"ok": True})


@login_required
def param_camera_save(request):
    """Salva e aplica na camera de verdade os controles V4L2/UVC. Espera JSON
    com os mesmos nomes de campo de CameraSettings; campos omitidos mantem o
    valor salvo anteriormente (permite salvar so um slider por vez)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    data = json.loads(request.body or "{}")
    cs = _ensure_camera_settings(project)

    for field in CAMERA_BOOL_FIELDS:
        if field in data:
            setattr(cs, field, bool(data[field]))
    for field in CAMERA_INT_FIELDS:
        if field in data:
            lo, hi = CAMERA_CONTROL_RANGES[field]
            try:
                setattr(cs, field, _clamp(int(data[field]), lo, hi))
            except (ValueError, TypeError):
                continue
    cs.save()

    ok, err = camera.apply_controls(_camera_settings_v4l2_dict(cs))
    return JsonResponse({"ok": ok, "error": err, "settings": _camera_settings_json(cs)})


@login_required
def param_camera_reset(request):
    """Restaura os controles V4L2/UVC pros valores de fabrica e aplica na camera."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    cs, _created = CameraSettings.objects.update_or_create(project=project, defaults=CAMERA_DEFAULTS)
    ok, err = camera.apply_controls(_camera_settings_v4l2_dict(cs))
    return JsonResponse({"ok": ok, "error": err, "settings": _camera_settings_json(cs)})


@login_required
def param_posicionar(request):
    """Vai ao fim de curso (referencia) e depois anda `steps` no sentido inverso
    ao homing, chegando na posicao do filtro."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    steps = min(abs(int(request.POST.get('steps', 0))), MAX_HOME_STEPS)
    pi = get_pi()
    _prepare_motor_move(pi)
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


def _detect_qr(frame):
    """Tenta detectar e decodificar um QR com dois detectores em sequencia --
    QRCodeDetectorAruco primeiro (pipeline baseado em deteccao de ArUco,
    bem mais robusto a angulo/distancia/foco/iluminacao reais que o
    QRCodeDetector classico -- confirmado com QRs sinteticos rotacionados,
    pequenos e desfocados: o classico falha sozinho em varios desses casos
    que o Aruco resolve, e vice-versa em alguns), caindo pro classico se o
    primeiro achar o quadrado mas nao conseguir LER o conteudo (sem
    conteudo nao da pra saber o tamanho em mm, entao so achar os 4 cantos
    nao basta). Se nenhum dos dois decodificar, ainda devolve os PONTOS do
    melhor achado (se algum localizou o quadrado), pra a UI poder desenhar
    onde ele acha que esta o QR mesmo sem ler o texto -- ajuda a diagnosticar
    (achou a posicao mas esta desfocado? nem achou o quadrado?)."""
    best_points = None
    for detector in (cv2.QRCodeDetectorAruco(), cv2.QRCodeDetector()):
        data, points, _ = detector.detectAndDecode(frame)
        if points is not None and best_points is None:
            best_points = points
        if points is not None and data:
            return data, points
    return "", best_points


def _qr_debug_payload(frame, points=None):
    """Frame capturado (PNG base64) + pontos (se algum detector achou o
    quadrado do QR, mesmo sem ler o conteudo) -- devolvido em TODA falha de
    param_geo_frame, pra dar pro usuario/UI algo visual pra diagnosticar em
    vez de so um texto de erro (estava escuro? o QR nem apareceu no quadro?
    ta desfocado?)."""
    ok_enc, buf = cv2.imencode('.png', frame)
    image_data = f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}" if ok_enc else ""
    payload = {"image_data": image_data, "width": int(frame.shape[1]), "height": int(frame.shape[0])}
    if points is not None:
        pts = np.array(points, dtype=float).reshape(-1, 2)
        payload["points"] = [[round(x, 1), round(y, 1)] for x, y in pts.tolist()]
    return payload


@login_required
def param_geo_frame(request):
    """Acende um LED (GEO_CAL_LED_NM por padrao, ou a banda escolhida no POST
    -- a caixa e fechada/escura, sem isso o QR nao aparece exposto o
    suficiente), captura um frame EM EXPOSICAO AUTOMATICA (ver
    capture_stabilized_auto -- mais robusto pra enxergar o QR do que
    depender de um tempo manual mal ajustado), detecta o QR (tamanho no
    conteudo) e calcula mm/pixel. Bandas diferentes iluminam um QR impresso
    de jeitos bem diferentes (LED/sensor mais ou menos eficiente naquele
    comprimento de onda) -- deixar escolher permite testar qual enxerga
    melhor na bancada de cada um, sem precisar mudar codigo."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    nm = request.POST.get('band') or GEO_CAL_LED_NM
    if nm not in LED_BANDS:
        nm = GEO_CAL_LED_NM

    pi = get_pi()
    pin = LED_BANDS[nm]
    bp = _band_capture_params(project).get(nm)
    _cancel_led_off(nm)
    for other_pin in LED_BANDS.values():
        _led_set(pi, other_pin, 0)
    frame = None
    try:
        _led_set(pi, pin, bp.intensity if bp else BAND_DEFAULT_INTENSITY)
        # exposicao SEMPRE automatica aqui, independente do tempo manual
        # configurado pra essa banda ou do toggle do projeto -- a calibracao
        # espacial so precisa ENXERGAR o QR bem exposto pra medir os 4 cantos
        # em pixels, essa foto nunca entra na conta de reflectancia (que e
        # quem precisa de exposicao fixa/comparavel entre bandas). Automatico
        # e mais robusto contra um tempo manual mal ajustado deixando a foto
        # escura ou estourada demais pro QRCodeDetector enxergar.
        frame, _exposure = camera.capture_stabilized_auto(BAND_AUTO_EXPOSE_SECONDS)
    finally:
        _led_set(pi, pin, 0)
        camera.release()
    if frame is None:
        return JsonResponse({"ok": False, "error": "Sem imagem da câmera."}, status=409)

    data, points = _detect_qr(frame)
    if points is None:
        return JsonResponse({"ok": False, "band": nm,
                             "error": "Nenhum QR detectado no frame — confira se ele está visível, "
                                      "focado e iluminado na imagem abaixo.",
                             **_qr_debug_payload(frame)}, status=422)

    qr_mm = _qr_mm_from_content(data)
    if qr_mm is None or qr_mm <= 0:
        error = (f"QR localizado, mas não deu pra ler o conteúdo ('{data}'). "
                 f"Codifique o lado em mm (ex.: '50')." if data else
                 "QR localizado (achei o quadrado), mas não consegui ler o conteúdo — "
                 "pode estar desfocado, pequeno ou com reflexo. Veja a imagem abaixo e reposicione.")
        return JsonResponse({"ok": False, "band": nm, "error": error,
                             **_qr_debug_payload(frame, points)}, status=422)

    pts = np.array(points, dtype=float).reshape(-1, 2)   # 4 cantos
    sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
    avg_px = sum(sides) / len(sides)
    deviation = (max(sides) - min(sides)) / avg_px if avg_px else 1.0
    mm_per_pixel = qr_mm / avg_px
    px_per_mm = avg_px / qr_mm

    ok_enc, buf = cv2.imencode('.png', frame)   # frame -> PNG em memoria
    qr_points = [[round(x, 1), round(y, 1)] for x, y in pts.tolist()]

    # uma calibracao espacial por projeto -- a nova SUBSTITUI a anterior
    # (nao acumula linha no banco a cada captura, ver models.py)
    cal, _created = GeometricCalibration.objects.update_or_create(
        project=project,
        defaults={"mm_per_pixel": mm_per_pixel, "px_per_mm": px_per_mm, "qr_mm": qr_mm,
                  "qr_px": avg_px, "qr_content": (data or "")[:200], "deviation": deviation,
                  "qr_points": qr_points})
    if ok_enc:
        if cal.image:
            cal.image.delete(save=False)   # apaga o arquivo antigo do disco, senao fica orfao
        cal.image.save(f"{_dated_name(project, 'geometrica')}.png", ContentFile(buf.tobytes()), save=False)
    cal.save()

    return JsonResponse({
        "ok": True,
        "band": nm,
        "mm_per_pixel": round(mm_per_pixel, 6),
        "px_per_mm": round(px_per_mm, 4),
        "qr_mm": qr_mm,
        "qr_px": round(avg_px, 1),
        "deviation_pct": round(deviation * 100, 1),
        "content": data,
        "tilted": deviation > 0.05,   # >5% de divergencia entre os lados = torto
        "image_url": cal.image.url if cal.image else "",
        "points": qr_points,   # 4 cantos do QR (pixels da imagem) -- pra desenhar o quadrilatero na UI
        "width": int(frame.shape[1]), "height": int(frame.shape[0]),
    })


@login_required
def param_reflectance_capture(request):
    """Tira uma foto por banda (LED_BANDS, brilho cheio) e guarda os frames em
    memoria (processo do Django). Retorna a foto da banda REFL_BASE_BAND (como
    data URL) para o usuario desenhar a bounding box da referencia 100%.

    Tambem salva uma copia de cada banda em
    projects/reflectance_parametrization/<timestamp>-<projeto>-reflectancia100/,
    so pra conferirmos as fotos tiradas (modo de teste)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    # Encerra qualquer stream MJPEG ativo: a camera UVC so aceita um leitor
    # por vez e, com o stream aberto, a captura pode travar ou entregar frames
    # invalidos. O botao na pagina ja para o stream; aqui e so a rede de seguranca.
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    pi = get_pi()
    bparams = _band_capture_params(project)
    cam = _ensure_camera_settings(project)
    for nm in LED_BANDS:
        _cancel_led_off(nm)  # evita apagar a banda no meio da exposicao
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)

    frames = {}
    base_frame = None
    try:
        for nm, pin in LED_BANDS.items():
            bp = bparams.get(nm)
            _led_set(pi, pin, bp.intensity if bp else BAND_DEFAULT_INTENSITY)
            # le frames de verdade (descartando) durante a estabilizacao, em
            # vez de dormir com a camera fechada -- e o que deixa o
            # auto-exposure convergir de fato (ver capture_stabilized). Exposicao
            # manual e por banda (BandParameter.exposure_time_absolute), a nao
            # ser que o toggle "automatica" (card Iluminacao) esteja ligado --
            # bandas com LED/sensor mais fraco ou mais forte raramente cabem
            # numa exposicao unica.
            frame = camera.capture_stabilized(LED_STABILIZE_SECONDS,
                exposure=_capture_exposure(cam, bp))
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
        camera.release()

    # copia de teste: 1 PNG por banda, mesmo nome/timestamp da captura
    folder = _dated_name(project, "reflectancia100")
    out_dir = os.path.join(settings.MEDIA_ROOT, "reflectance_parametrization", folder)
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
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
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

    # uma calibracao de reflectancia 100% por projeto -- a nova SUBSTITUI a
    # anterior (nao acumula linha no banco a cada captura/recalculo de bbox)
    cal, _created = ReflectanceCalibration.objects.update_or_create(
        project=project, defaults={"means": means, "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h})
    base_frame = frames.get(REFL_BASE_BAND)
    if base_frame is not None:
        ok_enc, buf = cv2.imencode('.png', base_frame)
        if ok_enc:
            if cal.image:
                cal.image.delete(save=False)
            cal.image.save(f"{_dated_name(project, 'reflectancia100')}.png", ContentFile(buf.tobytes()), save=False)
    cal.save()

    # Mantem a sessao/frames vivos: o usuario pode arrastar de novo pra ajustar
    # a bbox e recalcular quantas vezes quiser sobre as MESMAS fotos. So e
    # substituida quando uma nova captura acontecer (param_reflectance_capture).

    return JsonResponse({"ok": True, "means": means})


@login_required
def param_reflectance_zero(request):
    """Referencia escura (0%): 1 foto por banda, LEDs sempre apagados, mas
    cada foto tirada sob a MESMA exposicao manual configurada pra essa banda
    (BandParameter.exposure_time_absolute) -- ruido/corrente de escuro do
    sensor varia com o tempo de exposicao, entao com exposicao diferente por
    banda o 0% tambem precisa ser por banda (senao a formula
    (raw-dark)/(white-dark) fica inconsistente pras bandas com exposicao
    diferente da usada no 0% antigo, unico pra todas).

    Tambem salva uma copia de cada banda em
    projects/reflectance_parametrization/<timestamp>-<projeto>-reflectancia0/,
    igual a calibracao de 100%."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    pi = get_pi()
    bparams = _band_capture_params(project)
    cam = _ensure_camera_settings(project)
    for nm in LED_BANDS:
        _cancel_led_off(nm)
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)  # LEDs ficam apagados o tempo todo (referencia escura)

    frames = {}
    try:
        for nm in LED_BANDS:
            bp = bparams.get(nm)
            frame = camera.capture_stabilized(LED_STABILIZE_SECONDS,
                exposure=_capture_exposure(cam, bp))
            if frame is None:
                return JsonResponse({"ok": False,
                    "error": f"Sem imagem da câmera (banda {nm}nm)."}, status=409)
            frames[nm] = frame
    finally:
        camera.release()

    means = {nm: round(float(frame.mean()), 2) for nm, frame in frames.items()}

    # copia de teste: 1 PNG por banda, mesmo nome/timestamp da captura
    folder = _dated_name(project, "reflectancia0")
    out_dir = os.path.join(settings.MEDIA_ROOT, "reflectance_parametrization", folder)
    os.makedirs(out_dir, exist_ok=True)
    for nm, frame in frames.items():
        cv2.imwrite(os.path.join(out_dir, f"{nm}nm.png"), frame)
    print(f"Captura de reflectancia 0% salva em: {out_dir}")

    # uma calibracao de reflectancia 0% por projeto -- a nova SUBSTITUI a anterior
    cal, _created = ReflectanceZeroCalibration.objects.update_or_create(
        project=project, defaults={"means": means})
    base_frame = frames.get(REFL_BASE_BAND)
    image_data = ""
    if base_frame is not None:
        ok_enc, buf = cv2.imencode('.png', base_frame)
        if ok_enc:
            if cal.image:
                cal.image.delete(save=False)
            cal.image.save(f"{_dated_name(project, 'reflectancia0')}.png", ContentFile(buf.tobytes()), save=False)
            b64 = base64.b64encode(buf.tobytes()).decode('ascii')
            image_data = f"data:image/png;base64,{b64}"
    cal.save()

    return JsonResponse({"ok": True, "means": means, "image_data": image_data,
                         "width": int(base_frame.shape[1]) if base_frame is not None else 0,
                         "height": int(base_frame.shape[0]) if base_frame is not None else 0})


@login_required
def param_band_auto_expose(request):
    """Acende cada banda (no brilho ja configurado), deixa a exposicao
    automatica da camera convergir pra ela e SALVA o valor que a camera
    escolheu como o tempo de exposicao MANUAL dessa banda -- assim descobre o
    tempo ideal de cada uma sem chute manual, mas fica fixo/reproduzivel nas
    capturas de verdade dai em diante (ver _capture_exposure). Tambem desliga
    o toggle "exposicao automatica" do projeto, senao os valores recem-salvos
    seriam ignorados na proxima captura."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    pi = get_pi()
    bparams = _band_capture_params(project)
    for nm in LED_BANDS:
        _cancel_led_off(nm)
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)

    exposures = {}
    failed = []
    try:
        for nm, pin in LED_BANDS.items():
            bp = bparams.get(nm)
            _led_set(pi, pin, bp.intensity if bp else BAND_DEFAULT_INTENSITY)
            frame, exposure = camera.capture_stabilized_auto(BAND_AUTO_EXPOSE_SECONDS)
            _led_set(pi, pin, 0)
            time.sleep(1.5)  # espera o LED apagar antes da proxima banda
            if frame is not None and exposure is not None:
                exposures[nm] = exposure
            else:
                failed.append(nm)
    finally:
        for pin in LED_BANDS.values():
            _led_set(pi, pin, 0)
        camera.release()

    for nm, exposure in exposures.items():
        BandParameter.objects.filter(project=project, nm=nm).update(
            exposure_time_absolute=exposure, updated=timezone.now())
    if exposures:
        _ensure_camera_settings(project)
        CameraSettings.objects.filter(project=project).update(auto_exposure=False, updated=timezone.now())

    return JsonResponse({"ok": True, "exposures": exposures, "failed": failed})


# ─────────────────────────────────────────────────────────────
# Data Acquisition
# ─────────────────────────────────────────────────────────────
@login_required
@require_project
@require_calibrations
def data_acquisition(request):
    project = _get_active_project(request)
    acq = DataAcquisition.objects.filter(project=project).first()   # aquisicao mais recente DESSE projeto
    bands = [{"nm": nm, "color": LED_COLORS[nm]} for nm in LED_BANDS]
    acq_images = []
    rgb_url = ""
    if acq and acq.folder:
        for nm in LED_BANDS:
            raw_url = f"{settings.MEDIA_URL}acquisitions/{acq.folder}/raw/{nm}nm.png"
            # normalizada so existe pras bandas que tinham calibracao valida
            # no momento do salvamento -- confere o arquivo de verdade em
            # disco, nao um campo no banco (DataAcquisition nao guarda mais
            # means/reflectance do frame inteiro)
            norm_url = (f"{settings.MEDIA_URL}acquisitions/{acq.folder}/normalized/{nm}nm.png"
                        if _acquisition_has_normalized(acq, nm) else "")
            acq_images.append({"nm": nm, "raw_url": raw_url, "norm_url": norm_url})
        rgb_path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, "rgb.png")
        if os.path.exists(rgb_path):
            rgb_url = f"{settings.MEDIA_URL}acquisitions/{acq.folder}/rgb.png"
    return render(request, "pages/data_acquisition.html",
                  {"acq": acq, "bands": bands, "acq_images": acq_images, "project": project,
                   "led_stabilize_seconds": LED_STABILIZE_SECONDS, "rgb_url": rgb_url})


def _normalize_frame(frame, white_mean, dark_mean):
    """So pra EXIBICAO: reescala o frame (BGR uint8) linearmente usando a
    referencia dessa banda -- dark_mean vira 0, white_mean vira 255. A foto
    crua salva em disco nunca passa por isso. None tambem quando a faixa
    (branco-escuro) e curta demais pra confiar (ver MIN_REFLECTANCE_RANGE) --
    um denominador pequeno amplifica ruido do sensor em vez de mostrar a
    cena de verdade."""
    if white_mean is None or (white_mean - dark_mean) < MIN_REFLECTANCE_RANGE:
        return None
    scaled = (frame.astype(np.float32) - dark_mean) / (white_mean - dark_mean) * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


# RGB artificial ("natural color"): usa as 3 bandas mais proximas do visivel
# como canal R/G/B -- so pra VISUALIZACAO (galeria e a imagem de referencia
# onde o usuario seleciona os ROIs no Annotate). Os calculos de reflectancia
# continuam usando cada banda bruta individualmente, isso nunca entra neles.
RGB_COMPOSITE_BANDS = {"r": "660", "g": "520", "b": "460"}


def _rgb_composite(frames, refl, refl0):
    """Monta a composicao a partir dos frames em memoria (mesmos usados pra
    salvar raw/normalized). Usa a versao normalizada de cada canal quando ha
    calibracao (bandas ficam comparaveis em brilho entre si), senao cai pra
    crua. None se alguma das 3 bandas nao foi capturada."""
    channels = {}
    for ch, nm in RGB_COMPOSITE_BANDS.items():
        frame = frames.get(nm)
        if frame is None:
            return None
        norm = _normalize_frame(frame, refl.means.get(nm), refl0.means.get(nm)) if (refl and refl0) else None
        channels[ch] = norm if norm is not None else frame
    b = cv2.cvtColor(channels["b"], cv2.COLOR_BGR2GRAY)
    g = cv2.cvtColor(channels["g"], cv2.COLOR_BGR2GRAY)
    r = cv2.cvtColor(channels["r"], cv2.COLOR_BGR2GRAY)
    return cv2.merge([b, g, r])


# Captura fica em memoria ate o usuario confirmar com um nome (data_acquisition_save).
# Uma nova captura substitui a anterior se nao tiver sido salva.
_acquisition_capture = {}          # {"session": str, "frames": {nm: ndarray}, "means": {...}, "reflectance": {...}}
_acquisition_lock = threading.Lock()


@login_required
def data_acquisition_capture(request):
    """Tira 1 foto por banda (LED_BANDS, brilho cheio) -- mesma rotina da
    calibracao de reflectancia 100%. NAO salva nada ainda: guarda os frames
    em memoria e devolve, pra cada banda, a foto normalizada (branco/escuro
    de referencia -> 255/0) pra exibicao. So grava em disco/banco quando o
    usuario confirmar com um nome (ver data_acquisition_save)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    missing = _missing_calibrations(project)
    if missing:
        return JsonResponse({"ok": False,
            "error": "Complete a calibração antes de adquirir dados — falta: " + ", ".join(missing) + "."},
            status=409)
    if not _stop_active_stream():
        return JsonResponse({"ok": False,
            "error": "O stream anterior ainda está encerrando, tente de novo."}, status=503)

    pi = get_pi()
    bparams = _band_capture_params(project)
    cam = _ensure_camera_settings(project)
    for nm in LED_BANDS:
        _cancel_led_off(nm)
    for pin in LED_BANDS.values():
        _led_set(pi, pin, 0)

    means = {}
    frames = {}
    try:
        for nm, pin in LED_BANDS.items():
            bp = bparams.get(nm)
            _led_set(pi, pin, bp.intensity if bp else BAND_DEFAULT_INTENSITY)
            # le frames de verdade (descartando) durante a estabilizacao, em
            # vez de dormir com a camera fechada -- e o que deixa o
            # auto-exposure convergir de fato (ver capture_stabilized). Mesma
            # exposicao manual por banda usada na calibracao de reflectancia
            # (100% e 0%), senao a formula (raw-dark)/(white-dark) nao bate --
            # a nao ser que o toggle "automatica" (card Iluminacao) esteja ligado.
            frame = camera.capture_stabilized(LED_STABILIZE_SECONDS,
                exposure=_capture_exposure(cam, bp))
            _led_set(pi, pin, 0)
            time.sleep(1.5)
            if frame is None:
                return JsonResponse({"ok": False,
                    "error": f"Sem imagem da câmera (banda {nm}nm)."}, status=409)
            means[nm] = round(float(frame.mean()), 2)
            frames[nm] = frame
    finally:
        for pin in LED_BANDS.values():
            _led_set(pi, pin, 0)
        camera.release()

    refl = ReflectanceCalibration.objects.filter(project=project).first()
    refl0 = ReflectanceZeroCalibration.objects.filter(project=project).first()
    has_calibration = bool(refl and refl0)

    session = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with _acquisition_lock:
        _acquisition_capture.clear()
        _acquisition_capture['session'] = session
        _acquisition_capture['frames'] = frames

    images = {}
    for nm, frame in frames.items():
        ok_enc, buf = cv2.imencode('.png', frame)
        raw_data = (f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"
                    if ok_enc else None)
        norm_data = None
        if has_calibration:
            norm = _normalize_frame(frame, refl.means.get(nm), refl0.means.get(nm))
            if norm is not None:
                ok_enc2, buf2 = cv2.imencode('.png', norm)
                if ok_enc2:
                    norm_data = f"data:image/png;base64,{base64.b64encode(buf2.tobytes()).decode('ascii')}"
        images[nm] = {"raw": raw_data, "norm": norm_data}

    rgb_data = None
    rgb = _rgb_composite(frames, refl, refl0)
    if rgb is not None:
        ok_enc, buf = cv2.imencode('.png', rgb)
        if ok_enc:
            rgb_data = f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"

    return JsonResponse({"ok": True, "session": session, "means": means,
                         "has_calibration": has_calibration,
                         "images": images, "rgb": rgb_data})


@login_required
def data_acquisition_save(request):
    """Confirma a captura em memoria: grava a foto CRUA e a NORMALIZADA de
    cada banda em disco e cria o registro no banco (created = agora, o
    momento do salvamento -- nao o da captura)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    data = json.loads(request.body or "{}")
    name = (data.get('name') or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Dê um nome pra amostra antes de salvar."}, status=400)

    with _acquisition_lock:
        session = _acquisition_capture.get('session')
        frames = _acquisition_capture.get('frames')

    if not frames or data.get('session') != session:
        return JsonResponse({"ok": False,
            "error": "Captura expirada — adquira os dados de novo."}, status=409)

    folder = _dated_name(project, name)
    out_dir = os.path.join(settings.MEDIA_ROOT, "acquisitions", folder)
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "normalized"), exist_ok=True)

    refl = ReflectanceCalibration.objects.filter(project=project).first()
    refl0 = ReflectanceZeroCalibration.objects.filter(project=project).first()
    has_calibration = bool(refl and refl0)

    for nm, frame in frames.items():
        cv2.imwrite(os.path.join(out_dir, "raw", f"{nm}nm.png"), frame)
        if has_calibration:
            norm = _normalize_frame(frame, refl.means.get(nm), refl0.means.get(nm))
            if norm is not None:
                cv2.imwrite(os.path.join(out_dir, "normalized", f"{nm}nm.png"), norm)

    rgb = _rgb_composite(frames, refl, refl0)
    if rgb is not None:
        cv2.imwrite(os.path.join(out_dir, "rgb.png"), rgb)

    acq = DataAcquisition.objects.create(name=name, folder=folder, project=project)

    with _acquisition_lock:
        _acquisition_capture.clear()

    return JsonResponse({"ok": True, "id": acq.id, "name": acq.name,
                         "created": acq.created.isoformat(), "folder": folder})


@login_required
def data_acquisition_delete(request, acq_id):
    """Apaga uma aquisicao: registro no banco (Annotation e ROIMeasurement
    ligados somem junto, via CASCADE) + a pasta com as fotos em
    projects/acquisitions/<folder>/. Irreversivel -- so aceita apagar
    aquisicao do projeto ativo (nao um id de outro projeto)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    acq = DataAcquisition.objects.filter(id=acq_id, project=project).first()
    if not acq:
        return JsonResponse({"ok": False, "error": "Aquisição não encontrada."}, status=404)

    if acq.folder:
        folder_path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder)
        shutil.rmtree(folder_path, ignore_errors=True)

    acq.delete()
    return JsonResponse({"ok": True})


# Limite de aquisicoes por download em lote -- cada uma tem ~17 PNGs
# (raw + normalized + rgb), entao um zip sem limite pode ficar gigante e
# travar o navegador/servidor. 30 e um teto razoavel pra rodar num Raspberry Pi.
MAX_ZIP_DOWNLOAD_ACQUISITIONS = 30


@login_required
def data_acquisition_download_zip(request):
    """Baixa as pastas (raw/ + normalized/ + rgb.png) das aquisicoes
    selecionadas, uma por uma, dentro de um unico .zip -- mesma logica de
    selecao dos botoes de exportar (ids do projeto ativo). Escreve num
    arquivo temporario em disco (nao em memoria) pra nao estourar RAM com
    varias aquisicoes de uma vez."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    ids = (json.loads(request.body or "{}")).get('ids') or []
    if not ids:
        return JsonResponse({"ok": False, "error": "Nenhuma aquisição selecionada."}, status=400)
    if len(ids) > MAX_ZIP_DOWNLOAD_ACQUISITIONS:
        return JsonResponse({"ok": False,
            "error": f"Máximo de {MAX_ZIP_DOWNLOAD_ACQUISITIONS} imagens por download "
                     f"(selecionadas: {len(ids)})."}, status=400)

    acquisitions = list(DataAcquisition.objects.filter(id__in=ids, project=project))
    if not acquisitions:
        return JsonResponse({"ok": False, "error": "Nenhuma aquisição encontrada."}, status=404)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for acq in acquisitions:
            if not acq.folder:
                continue
            folder_path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder)
            if not os.path.isdir(folder_path):
                continue
            root_name = acq.folder   # ja e unico/datado (ver _dated_name), sem colisao entre amostras
            for dirpath, _dirnames, filenames in os.walk(folder_path):
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    arcname = os.path.join(root_name, os.path.relpath(fpath, folder_path))
                    zf.write(fpath, arcname)
    tmp.seek(0)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    response = FileResponse(tmp, as_attachment=True, filename=f"aquisicoes_{stamp}.zip")
    return response


# ─────────────────────────────────────────────────────────────
# Projetos & Analysis -- port da annotation tool (Resonon-Pika-L-...)
#
# Diferencas do app original (Flask + arquivos):
#  - "projeto" agrupa DataAcquisition (nao mais uma pasta de .hdr no disco).
#  - anotacao e sempre feita na banda de referencia fixa REFL_BASE_BAND (a
#    camera e fixa, entao a mesma regiao vale conceitualmente pras 8 bandas).
#  - projeto ativo fica na sessao do navegador (nao um config.json global).
#  - modelos YOLO sao enviados por upload (FileField) em vez de um caminho
#    local escolhido com dialogo do tkinter -- o app roda num servidor
#    acessado pelo navegador, o dialogo local nao faz sentido aqui.
#  - ultralytics (YOLO) e importado tardiamente: se nao estiver instalado,
#    o resto do app continua funcionando normalmente.
# ─────────────────────────────────────────────────────────────
ANNOTATION_BAND = REFL_BASE_BAND  # banda usada como base pra anotar/segmentar


def _acquisition_has_normalized(acq, nm):
    """Confere se a versao normalizada dessa banda existe de verdade em
    disco -- DataAcquisition nao guarda mais um dict indicando quais bandas
    tinham calibracao no momento do salvamento (ver ROIMeasurement)."""
    if not acq.folder:
        return False
    path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, "normalized", f"{nm}nm.png")
    return os.path.exists(path)


def _acquisition_ref_subdir(acq):
    return "normalized" if _acquisition_has_normalized(acq, ANNOTATION_BAND) else "raw"


def _acquisition_rgb_path(acq):
    if not acq.folder:
        return None
    path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, "rgb.png")
    return path if os.path.exists(path) else None


# ── Medicao por ROI (ROIMeasurement) ────────────────────────────────────────
# Elipse/circulo desenhados no Annotate sempre viram poligono com EXATAMENTE
# ANNOTATE_ELLIPSE_PTS pontos (ver ELLIPSE_PTS/ellipseToPolygon em
# annotate.html) -- um poligono organico (mao livre + simplificacao) quase
# sempre tem uma contagem diferente. Isso classifica a forma sem precisar de
# nenhum estado novo no JS de desenho (undo/redo, edicao de vertice etc.
# continuam intocados).
ANNOTATE_ELLIPSE_PTS = 32
ROI_POINT_RADIUS_PX = 8   # janela (em pixels) ao redor de um ROI tipo ponto, pra tirar a media


def _classify_polygon_shape(points):
    if len(points) != ANNOTATE_ELLIPSE_PTS:
        return ROIMeasurement.SelectionMethod.POLYGON
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return ROIMeasurement.SelectionMethod.POLYGON
    ratio = min(w, h) / max(w, h)
    return ROIMeasurement.SelectionMethod.CIRCLE if ratio >= 0.92 else ROIMeasurement.SelectionMethod.ELLIPSE


def _roi_mean(gray, contour):
    """Media (0-255) dos pixels de `gray` (array 2D) dentro do ROI descrito
    por `contour`. None se a regiao ficar vazia/fora da imagem."""
    h, w = gray.shape[:2]
    kind = contour.get("shape")
    if kind == "box":
        x0, x1 = sorted((contour["x1"], contour["x2"]))
        y0, y1 = sorted((contour["y1"], contour["y2"]))
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(w, int(x1)), min(h, int(y1))
        crop = gray[y0:y1, x0:x1]
        return float(crop.mean()) if crop.size else None
    if kind == "point":
        r = ROI_POINT_RADIUS_PX
        cx, cy = int(contour["x"]), int(contour["y"])
        x0, y0 = max(0, cx - r), max(0, cy - r)
        x1, y1 = min(w, cx + r), min(h, cy + r)
        crop = gray[y0:y1, x0:x1]
        return float(crop.mean()) if crop.size else None
    pts = contour.get("points") or []
    if len(pts) < 3:
        return None
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(pts, dtype=np.int32)], 255)
    vals = gray[mask > 0]
    return float(vals.mean()) if vals.size else None


def _roi_reflectance_pct(raw, white, dark):
    """None quando: a faixa (branco-escuro) e curta demais pra confiar (ver
    MIN_REFLECTANCE_RANGE); o bruto da amostra ou do proprio branco de
    referencia esta literalmente SATURADO no sensor (ver
    SATURATION_RAW_THRESHOLD); ou o resultado passa de REFLECTANCE_MAX_PCT
    (pega tambem o caso sem clipping mas ainda fisicamente implausivel --
    normalmente reflexo especular). Em qualquer um desses casos a media do
    ROI nao representa reflectancia difusa de verdade, entao nao faz sentido
    devolver um numero como se fosse confiavel."""
    if white is None or dark is None or (white - dark) < MIN_REFLECTANCE_RANGE:
        return None
    if raw >= SATURATION_RAW_THRESHOLD or white >= SATURATION_RAW_THRESHOLD:
        return None
    pct = (raw - dark) / (white - dark) * 100
    if pct > REFLECTANCE_MAX_PCT:
        return None
    return round(pct, 2)


@login_required
def roi_measurement_compute(request, acq_id):
    """Calcula a media por banda de cada ROI desenhado no Annotate (boxes,
    poligonos -- classificados em circulo/elipse/organico -- e pontos) e
    SUBSTITUI as ROIMeasurement dessa aquisicao pelas novas (apaga as
    antigas primeiro, senao acumula linha obsoleta toda vez que o usuario
    redesenha e recalcula -- mesma logica das calibracoes unicas)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    project = _get_active_project(request)
    if not project:
        return JsonResponse({"ok": False, "error": "Selecione um projeto na Home."}, status=400)
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"ok": False, "error": "Aquisição não encontrada."}, status=404)
    if acq.project_id != project.id:
        return JsonResponse({"ok": False, "error": "Essa aquisição pertence a outro projeto."}, status=403)

    ann = getattr(acq, 'annotation', None)
    if not ann or not (ann.boxes or ann.polygons or ann.points):
        return JsonResponse({"ok": False,
            "error": "Nenhum ROI desenhado ainda — use box/elipse/polígono/ponto no Annotate."}, status=400)
    if not acq.folder:
        return JsonResponse({"ok": False, "error": "Aquisição sem fotos salvas."}, status=409)

    subdir = _acquisition_ref_subdir(acq)   # mesma banda/pasta usada como imagem de referencia visual
    frames = {}
    for nm in LED_BANDS:
        path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, subdir, f"{nm}nm.png")
        if not os.path.exists(path):
            path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, "raw", f"{nm}nm.png")
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            frames[nm] = img
    if not frames:
        return JsonResponse({"ok": False, "error": "Nenhuma foto encontrada em disco pra essa aquisição."}, status=409)

    refl = ReflectanceCalibration.objects.filter(project=project).first()
    refl0 = ReflectanceZeroCalibration.objects.filter(project=project).first()
    has_calibration = bool(refl and refl0)

    rois = []   # [(method, label, contour), ...]
    for box, lbl in zip(ann.boxes, ann.labels or [""] * len(ann.boxes)):
        rois.append((ROIMeasurement.SelectionMethod.BOX, lbl,
                    {"shape": "box", "x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]}))
    for pts, lbl in zip(ann.polygons, ann.poly_labels or [""] * len(ann.polygons)):
        method = _classify_polygon_shape(pts)
        rois.append((method, lbl, {"shape": method, "points": pts}))
    for pt, lbl in zip(ann.points, ann.point_labels or [""] * len(ann.points)):
        rois.append((ROIMeasurement.SelectionMethod.POINT, lbl,
                    {"shape": "point", "x": pt[0], "y": pt[1]}))

    counters = {}
    rows = []
    for method, lbl, contour in rois:
        counters[method] = counters.get(method, 0) + 1
        means = {}
        for nm, gray in frames.items():
            v = _roi_mean(gray, contour)
            if v is not None:
                means[nm] = round(v, 2)
        reflectance = {}
        if has_calibration:
            for nm, raw in means.items():
                pct = _roi_reflectance_pct(raw, refl.means.get(nm), refl0.means.get(nm))
                if pct is not None:
                    reflectance[nm] = pct
        rows.append(ROIMeasurement(
            project=project, acquisition=acq, method=method, index=counters[method],
            label=lbl or "", contour=contour, means=means, reflectance=reflectance))

    ROIMeasurement.objects.filter(acquisition=acq).delete()
    ROIMeasurement.objects.bulk_create(rows)

    by_method = {}
    for r in rows:
        by_method[r.method] = by_method.get(r.method, 0) + 1

    return JsonResponse({"ok": True, "total": len(rows), "by_method": by_method})


def _acquisition_ref_image_url(acq):
    """Imagem usada como fundo pra desenhar os ROIs (Annotate) e como
    thumbnail (Analysis). Prefere o RGB artificial (660/520/460 -> R/G/B,
    ver _rgb_composite) por ser bem mais facil de enxergar as sementes do que
    uma banda unica em cinza; cai pra ANNOTATION_BAND se o rgb.png nao existir
    (aquisicao antiga, de antes dessa composicao existir)."""
    if not acq.folder:
        return ""
    if _acquisition_rgb_path(acq):
        return f"{settings.MEDIA_URL}acquisitions/{acq.folder}/rgb.png"
    subdir = _acquisition_ref_subdir(acq)
    return f"{settings.MEDIA_URL}acquisitions/{acq.folder}/{subdir}/{ANNOTATION_BAND}nm.png"


def _acquisition_ref_image_path(acq):
    if not acq.folder:
        return None
    rgb_path = _acquisition_rgb_path(acq)
    if rgb_path:
        return rgb_path
    subdir = _acquisition_ref_subdir(acq)
    path = os.path.join(settings.MEDIA_ROOT, "acquisitions", acq.folder, subdir, f"{ANNOTATION_BAND}nm.png")
    return path if os.path.exists(path) else None


def _load_ref_image(acq):
    """Le a imagem de referencia (BGR, como o cv2 espera) direto do disco."""
    path = _acquisition_ref_image_path(acq)
    if not path:
        return None
    return cv2.imread(path)


def _acq_image_dims(acq):
    img = _load_ref_image(acq)
    if img is None:
        return None
    h, w = img.shape[:2]
    return w, h


# ── Projetos ─────────────────────────────────────────────────────────────────

@login_required
def project_create(request):
    """Cria um projeto. Se `source_id` vier no corpo, clona a parametrizacao
    (filtros + intensidade dos LEDs) desse outro projeto; senao, comeca
    zerado. Calibracoes (espacial/reflectancia) NUNCA sao clonadas -- cada
    projeto precisa da sua propria, feita na hora, contra o setup fisico
    atual."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    data = json.loads(request.body or "{}")
    name = (data.get('name') or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Nome é obrigatório"}, status=400)
    if Project.objects.filter(name=name).exists():
        return JsonResponse({"ok": False, "error": "Já existe um projeto com esse nome"}, status=400)

    source = Project.objects.filter(id=data.get('source_id')).first() if data.get('source_id') else None

    project = Project.objects.create(name=name)
    if source:
        for f in FilterPosition.objects.filter(project=source):
            FilterPosition.objects.create(project=project, index=f.index, name=f.name, steps=f.steps)
        for b in BandParameter.objects.filter(project=source):
            BandParameter.objects.create(project=project, nm=b.nm, order=b.order, intensity=b.intensity,
                                         exposure_time_absolute=b.exposure_time_absolute)
    _ensure_param_rows(project)  # completa o que nao veio do clone (ou zera tudo, se nao clonou)

    request.session['active_project_id'] = project.id
    return JsonResponse({"ok": True, "id": project.id, "name": project.name})


@login_required
def project_select(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    data = json.loads(request.body or "{}")
    project = Project.objects.filter(id=data.get('id')).first()
    if not project:
        return JsonResponse({"ok": False, "error": "Projeto não encontrado"}, status=404)
    request.session['active_project_id'] = project.id
    return JsonResponse({"ok": True})


@login_required
def project_delete(request, project_id):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    was_default = Project.objects.filter(id=project_id, is_default=True).exists()
    Project.objects.filter(id=project_id).delete()
    # aquisicoes/calibracoes que pertenciam a esse projeto NAO sao apagadas
    # (FK SET_NULL); filtros/bandas dele, sim (FK CASCADE -- sao so config).
    if was_default:
        # sempre precisa sobrar um projeto padrao pra clonar em projetos novos
        promoted = Project.objects.order_by('id').first()
        if promoted:
            promoted.is_default = True
            promoted.save(update_fields=['is_default'])
    if request.session.get('active_project_id') == project_id:
        remaining = Project.objects.first()
        request.session['active_project_id'] = remaining.id if remaining else None
    return JsonResponse({"ok": True})


@login_required
def project_model_upload(request, project_id):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    model_type = request.POST.get('type')
    if model_type not in ('seg', 'det', 'cls'):
        return JsonResponse({"ok": False, "error": "type deve ser 'seg', 'det' ou 'cls'"}, status=400)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({"ok": False, "error": "Nenhum arquivo enviado"}, status=400)
    project = Project.objects.filter(id=project_id).first()
    if not project:
        return JsonResponse({"ok": False, "error": "Projeto não encontrado"}, status=404)
    setattr(project, f"model_{model_type}", f)
    project.save()
    return JsonResponse({"ok": True, "type": model_type, "name": f.name})


# ── Analysis (grade de aquisicoes do projeto ativo) ───────────────────────────

@login_required
@require_project
def analysis(request):
    project = _get_active_project(request)
    acquisitions = []
    all_labels = set()
    if project:
        for acq in project.acquisitions.all():
            ann = getattr(acq, 'annotation', None)
            n_boxes = len(ann.boxes) if ann else 0
            n_polygons = len(ann.polygons) if ann else 0
            n_points = len(ann.points) if ann else 0
            image_label = ann.image_label if ann else ""
            if image_label:
                all_labels.add(image_label)
            acquisitions.append({
                "id": acq.id,
                "name": acq.name or f"Aquisição {acq.id}",
                "created": acq.created,
                "thumb_url": _acquisition_ref_image_url(acq),
                "annotated": n_boxes > 0 or n_polygons > 0 or n_points > 0,
                "n_boxes": n_boxes, "n_polygons": n_polygons, "n_points": n_points,
                "n_rois": acq.roi_measurements.count(),
                "image_label": image_label,
            })
    total = len(acquisitions)
    annotated = sum(1 for a in acquisitions if a["annotated"])
    progress_pct = round(annotated / total * 100, 1) if total else 0
    has_models = {"seg": bool(project and project.model_seg),
                  "det": bool(project and project.model_det),
                  "cls": bool(project and project.model_cls)}
    return render(request, "pages/analysis.html", {
        "project": project, "acquisitions": acquisitions, "total": total,
        "annotated": annotated, "progress_pct": progress_pct,
        "all_labels": sorted(all_labels), "has_models": has_models,
    })


@login_required
@require_project
def roi_measurements_view(request):
    """Tabela com a medicao de cada ROI (imagem, metodo e numero do ROI
    sempre juntos na mesma linha -- 'linkado', como pedido) do projeto ativo.
    Cada ROI vira 2 linhas na tabela quando tem reflectancia calculada (bruto
    0-255 + reflectancia %, ambos visiveis lado a lado) -- so 1 linha (bruto)
    quando nao havia calibracao no momento da medicao. As colunas
    Imagem/Metodo/ROI/Label sao mescladas (rowspan) entre as 2 linhas da
    mesma medicao, pra nao repetir."""
    project = _get_active_project(request)
    band_list = list(LED_BANDS.keys())
    rois = list(ROIMeasurement.objects.filter(project=project)
                .select_related('acquisition')
                .order_by('acquisition__name', 'method', 'index'))
    rows = []
    for r in rois:
        base = {
            "image": r.acquisition.name or f"acq_{r.acquisition_id}",
            "acq_id": r.acquisition_id,
            "method": r.get_method_display(),
            "method_raw": r.method,
            "index": r.index,
            "label": r.label,
        }
        has_refl = bool(r.reflectance)
        rows.append({**base, "is_first": True, "row_span": 2 if has_refl else 1,
                     "is_reflectance": False,
                     "values": [r.means.get(nm) for nm in band_list]})
        if has_refl:
            rows.append({**base, "is_first": False, "row_span": 1,
                         "is_reflectance": True,
                         "values": [r.reflectance.get(nm) for nm in band_list]})
    methods = sorted({r.get_method_display() for r in rois})
    return render(request, "pages/roi_measurements.html", {
        "project": project, "bands": band_list, "rows": rows, "total": len(rois),
        "methods": methods,
    })


@login_required
def export_roi_measurements(request):
    """Exporta as medicoes por ROI (das aquisicoes selecionadas) em CSV --
    Imagem | Método | ROI | Label | Tipo | <banda>nm... -- mesma ideia da
    planilha de referencia (Imagem | Método | Semente | <bandas>). CSV em vez
    de .xlsx pra nao depender de uma lib nova (openpyxl) so pra isso; abre
    normal no Excel/LibreOffice/pandas. Cada ROI vira 2 linhas (bruto +
    reflectância %) quando tinha calibração no momento da medição -- 1 linha
    (bruto) quando não tinha, igual a tela de Medições por ROI."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    ids = (json.loads(request.body or "{}")).get('ids') or []
    rois = (ROIMeasurement.objects.filter(acquisition_id__in=ids)
            .select_related('acquisition').order_by('acquisition__name', 'method', 'index'))

    band_list = list(LED_BANDS.keys())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Imagem", "Método", "ROI", "Label", "Tipo"] + [f"{nm}nm" for nm in band_list])
    n_rows = 0
    for r in rois:
        base = [r.acquisition.name or f"acq_{r.acquisition_id}", r.get_method_display(), r.index, r.label]
        writer.writerow(base + ["bruto_0-255"] + [r.means.get(nm, "") for nm in band_list])
        n_rows += 1
        if r.reflectance:
            writer.writerow(base + ["reflectância_%"] + [r.reflectance.get(nm, "") for nm in band_list])
            n_rows += 1

    return JsonResponse({"csv": buf.getvalue(), "count": n_rows})


@login_required
def annotation_image_label(request, acq_id):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"ok": False, "error": "Aquisição não encontrada"}, status=404)
    data = json.loads(request.body or "{}")
    ann, _ = Annotation.objects.get_or_create(acquisition=acq)
    ann.image_label = (data.get('label') or "").strip()
    ann.save()
    return JsonResponse({"ok": True, "label": ann.image_label})


# ── Editor de anotação ────────────────────────────────────────────────────────

@login_required
@require_project
def annotate_view(request, acq_id):
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        from django.http import Http404
        raise Http404("Aquisição não encontrada")
    active_project = _get_active_project(request)
    if acq.project_id != active_project.id:
        messages.warning(request, "Essa amostra pertence a outro projeto.")
        return redirect('analysis')
    ann, _ = Annotation.objects.get_or_create(acquisition=acq)
    project = acq.project
    siblings = list(project.acquisitions.values_list('id', flat=True)) if project else [acq.id]
    idx = siblings.index(acq.id) if acq.id in siblings else 0
    prev_id = siblings[idx - 1] if idx > 0 else None
    next_id = siblings[idx + 1] if idx < len(siblings) - 1 else None
    models_available = {"seg": bool(project and project.model_seg),
                        "det": bool(project and project.model_det),
                        "cls": bool(project and project.model_cls)}
    # miniaturas das 8 bandas CRUAS (nao normalizadas) -- normalizar
    # reescala/clampa a imagem, o que pode disfarcar saturacao real do
    # sensor; a foto crua e o jeito confiavel de ver isso a olho.
    band_thumbs = [{"nm": nm, "color": LED_COLORS[nm],
                    "url": f"{settings.MEDIA_URL}acquisitions/{acq.folder}/raw/{nm}nm.png"}
                   for nm in LED_BANDS] if acq.folder else []
    return render(request, "pages/annotate.html", {
        "acq": acq, "ann": ann,
        "ref_image_url": _acquisition_ref_image_url(acq),
        "band_thumbs": band_thumbs,
        "prev_id": prev_id, "next_id": next_id,
        "current": idx + 1, "total": len(siblings),
        "models": models_available,
    })


@login_required
def annotation_save(request, acq_id):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"ok": False, "error": "Aquisição não encontrada"}, status=404)
    data = json.loads(request.body or "{}")
    boxes = data.get('boxes', [])
    labels = data.get('labels', [""] * len(boxes))
    polygons = data.get('polygons', [])
    poly_labels = data.get('poly_labels', [""] * len(polygons))
    points = data.get('points', [])
    point_labels = data.get('point_labels', [""] * len(points))
    ann, _ = Annotation.objects.get_or_create(acquisition=acq)
    ann.boxes, ann.labels = boxes, labels
    ann.polygons, ann.poly_labels = polygons, poly_labels
    ann.points, ann.point_labels = points, point_labels
    if 'image_label' in data:
        ann.image_label = (data.get('image_label') or "").strip()
    ann.save()
    return JsonResponse({"ok": True, "saved": len(boxes),
                         "n_polygons": len(polygons), "n_points": len(points)})


# ── Exportacao ────────────────────────────────────────────────────────────────

@login_required
def export_yolo_boxes(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    ids = (json.loads(request.body or "{}")).get('ids') or []
    anns = [a for a in Annotation.objects.filter(acquisition_id__in=ids).select_related('acquisition') if a.boxes]

    all_labels = set()
    for a in anns:
        all_labels.update(a.labels or [])
    label_map = {lbl: i for i, lbl in enumerate(sorted(all_labels))}
    output = {"label_map": label_map, "acquisitions": {}, "annotations": {}}

    for a in anns:
        dims = _acq_image_dims(a.acquisition)
        if not dims:
            continue
        img_w, img_h = dims
        lines = []
        for (x1, y1, x2, y2), lbl in zip(a.boxes, a.labels or [""] * len(a.boxes)):
            cls = label_map.get(lbl, 0)
            cx, cy = ((x1 + x2) / 2) / img_w, ((y1 + y2) / 2) / img_h
            bw, bh = (x2 - x1) / img_w, (y2 - y1) / img_h
            lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        key = a.acquisition.name or f"acq_{a.acquisition.id}"
        output["acquisitions"][key] = a.acquisition.id
        output["annotations"][key] = lines
    return JsonResponse(output)


@login_required
def export_yolo_seg(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    ids = (json.loads(request.body or "{}")).get('ids') or []
    anns = [a for a in Annotation.objects.filter(acquisition_id__in=ids).select_related('acquisition') if a.polygons]

    all_labels = set()
    for a in anns:
        all_labels.update(a.poly_labels or [])
    label_map = {lbl: i for i, lbl in enumerate(sorted(all_labels))}
    output = {"label_map": label_map, "acquisitions": {}, "annotations": {}}

    for a in anns:
        dims = _acq_image_dims(a.acquisition)
        if not dims:
            continue
        img_w, img_h = dims
        lines = []
        for poly, lbl in zip(a.polygons, a.poly_labels or [""] * len(a.polygons)):
            cls = label_map.get(lbl, 0)
            pts_norm = " ".join(f"{x/img_w:.6f} {y/img_h:.6f}" for x, y in poly)
            lines.append(f"{cls} {pts_norm}")
        key = a.acquisition.name or f"acq_{a.acquisition.id}"
        output["acquisitions"][key] = a.acquisition.id
        output["annotations"][key] = lines
    return JsonResponse(output)


@login_required
def export_points(request):
    ids = None
    if request.method == 'POST':
        ids = (json.loads(request.body or "{}")).get('ids')
    qs = Annotation.objects.select_related('acquisition').all()
    if ids:
        qs = qs.filter(acquisition_id__in=ids)
    output = {"annotations": {}}
    for a in qs:
        if not a.points:
            continue
        dims = _acq_image_dims(a.acquisition)
        if not dims:
            continue
        img_w, img_h = dims
        labels = a.point_labels or [""] * len(a.points)
        key = a.acquisition.name or f"acq_{a.acquisition.id}"
        output["annotations"][key] = [
            {"x": x, "y": y, "x_norm": x / img_w, "y_norm": y / img_h, "label": lbl}
            for (x, y), lbl in zip(a.points, labels)
        ]
    return JsonResponse(output)


@login_required
def export_classification(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    ids = (json.loads(request.body or "{}")).get('ids') or []
    label_map = {}
    samples = []
    anns = Annotation.objects.filter(acquisition_id__in=ids).select_related('acquisition').order_by('acquisition_id')
    for a in anns:
        img_label = (a.image_label or "").strip()
        if not img_label:
            continue
        if img_label not in label_map:
            label_map[img_label] = len(label_map)
        samples.append({"id": a.acquisition.id, "name": a.acquisition.name or f"acq_{a.acquisition.id}",
                        "label": img_label, "class_id": label_map[img_label]})
    output = {"label_map": label_map,
             "acquisitions": {s["name"]: s["id"] for s in samples},
             "annotations": {s["name"]: s["class_id"] for s in samples}}
    return JsonResponse(output)


# ── Segmentação/deteção/classificação automática ──────────────────────────────

def _otsu_contour_from_crop(crop_gray, min_area):
    """Melhor contorno de Otsu (normal ou invertido) dentro de um recorte."""
    blur = cv2.GaussianBlur(crop_gray, (5, 5), 0)
    _, thresh_norm = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh_inv = cv2.bitwise_not(thresh_norm)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    thresh_norm = cv2.morphologyEx(thresh_norm, cv2.MORPH_CLOSE, k)
    thresh_inv = cv2.morphologyEx(thresh_inv, cv2.MORPH_CLOSE, k)

    def best(thresh):
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        return c if cv2.contourArea(c) >= min_area else None

    c_norm, c_inv = best(thresh_norm), best(thresh_inv)
    if c_norm is None and c_inv is None:
        return None
    if c_norm is None:
        return c_inv
    if c_inv is None:
        return c_norm
    return c_norm if cv2.contourArea(c_norm) >= cv2.contourArea(c_inv) else c_inv


def _contour_from_box(img, x1, y1, x2, y2):
    h, w = img.shape[:2]
    cx1, cy1 = max(0, x1), max(0, y1)
    cx2, cy2 = min(w, x2), min(h, y2)
    if cx2 <= cx1 or cy2 <= cy1:
        return None
    crop = img[cy1:cy2, cx1:cx2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    c = _otsu_contour_from_crop(gray, (cx2 - cx1) * (cy2 - cy1) * 0.15)
    if c is None:
        return None
    epsilon = 0.008 * cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, epsilon, True)
    pts = approx.reshape(-1, 2)
    if len(pts) < 8:
        raw = c.reshape(-1, 2)
        step = max(1, len(raw) // 40)
        pts = raw[::step]
    if len(pts) < 8:
        return None
    pts = pts + np.array([cx1, cy1], dtype=np.int32)
    return pts.tolist()


@login_required
def auto_segment_otsu(request, acq_id):
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    img = _load_ref_image(acq)
    if img is None:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        thresh = cv2.bitwise_not(thresh)
        kernel = np.ones((3, 3), np.uint8)
        opening = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=2)
        contours, _ = cv2.findContours(opening, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        img_h, img_w = gray.shape[:2]
        img_area = img_h * img_w
        min_area, max_area = img_area * 0.0005, img_area * 0.08
        valid = [c for c in contours if min_area <= cv2.contourArea(c) <= max_area]
        if not valid:
            return JsonResponse({"polygons": [], "count": 0})

        heights = sorted([cv2.boundingRect(c)[3] for c in valid])
        median_h = heights[len(heights) // 2]
        tolerance_y = max(10, median_h // 2)
        valid.sort(key=lambda c: cv2.boundingRect(c)[1])
        rows, current = [], []
        for c in valid:
            y = cv2.boundingRect(c)[1]
            if not current or abs(y - cv2.boundingRect(current[0])[1]) <= tolerance_y:
                current.append(c)
            else:
                rows.append(current); current = [c]
        if current:
            rows.append(current)
        sorted_contours = []
        for row in rows:
            row.sort(key=lambda c: cv2.boundingRect(c)[0])
            sorted_contours.extend(row)

        polygons = []
        for c in sorted_contours:
            epsilon = 0.008 * cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, epsilon, True)
            pts = approx.reshape(-1, 2).tolist()
            if len(pts) < 8:
                raw = c.reshape(-1, 2)
                step = max(1, len(raw) // 40)
                pts = raw[::step].tolist()
            if len(pts) >= 4:
                polygons.append(pts)
        return JsonResponse({"polygons": polygons, "count": len(polygons)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def auto_polygon_from_boxes(request, acq_id):
    """Gera poligonos a partir das boxes existentes via Otsu (equivalente ao
    auto_annotate.py do app original, mas sob demanda por aquisicao)."""
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    img = _load_ref_image(acq)
    if img is None:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    data = json.loads(request.body or "{}")
    boxes = data.get('boxes') or []
    labels = data.get('labels') or [""] * len(boxes)
    if not boxes:
        return JsonResponse({"error": "Sem boxes pra converter."}, status=400)
    new_polys, new_labels = [], []
    for (x1, y1, x2, y2), lbl in zip(boxes, labels):
        pts = _contour_from_box(img, x1, y1, x2, y2)
        if pts:
            new_polys.append(pts)
            new_labels.append(lbl)
    return JsonResponse({"polygons": new_polys, "poly_labels": new_labels, "count": len(new_polys)})


@login_required
def auto_segment_det(request, acq_id):
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    project = acq.project
    if not project or not project.model_det:
        return JsonResponse({"error": "Modelo de detecção não configurado."}, status=404)
    img = _load_ref_image(acq)
    if img is None:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    try:
        from ultralytics import YOLO
    except ImportError:
        return JsonResponse({"error": "ultralytics não está instalado neste servidor (pip install ultralytics)."}, status=500)
    try:
        results = YOLO(project.model_det.path)(img, verbose=False)[0]
        dets = [tuple(int(v) for v in xyxy) for xyxy in results.boxes.xyxy.cpu().numpy().astype(int)]

        heights = sorted([(y2 - y1) for x1, y1, x2, y2 in dets]) if dets else [1]
        median_h = heights[len(heights) // 2]
        tolerance_y = max(10, median_h // 2)
        dets.sort(key=lambda d: d[1])
        rows, cur = [], []
        for d in dets:
            if not cur or abs(d[1] - cur[0][1]) <= tolerance_y:
                cur.append(d)
            else:
                rows.append(cur); cur = [d]
        if cur:
            rows.append(cur)
        sorted_dets = []
        for row in rows:
            row.sort(key=lambda d: d[0])
            sorted_dets.extend(row)
        return JsonResponse({"boxes": sorted_dets, "count": len(sorted_dets)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def auto_segment_yolo(request, acq_id):
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    project = acq.project
    if not project or not project.model_seg:
        return JsonResponse({"error": "Modelo de segmentação não configurado. Importe um modelo no painel de projetos."}, status=404)
    img = _load_ref_image(acq)
    if img is None:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    try:
        from ultralytics import YOLO
    except ImportError:
        return JsonResponse({"error": "ultralytics não está instalado neste servidor (pip install ultralytics)."}, status=500)
    try:
        model = YOLO(project.model_seg.path)
        results = model(img, verbose=False)[0]
        img_h, img_w = img.shape[:2]
        if results.masks is None:
            return JsonResponse({"polygons": [], "count": 0})

        detections = []
        for mask_data in results.masks.data.cpu().numpy():
            mask_u8 = (mask_data * 255).astype(np.uint8)
            if mask_u8.shape != (img_h, img_w):
                mask_u8 = cv2.resize(mask_u8, (img_w, img_h))
            cnts, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            detections.append((x, y, w, h, c))
        if not detections:
            return JsonResponse({"polygons": [], "count": 0})

        heights = sorted([d[3] for d in detections])
        median_h = heights[len(heights) // 2]
        tolerance_y = max(10, median_h // 2)
        detections.sort(key=lambda d: d[1])
        rows, current = [], []
        for d in detections:
            if not current or abs(d[1] - current[0][1]) <= tolerance_y:
                current.append(d)
            else:
                rows.append(current); current = [d]
        if current:
            rows.append(current)
        sorted_detections = []
        for row in rows:
            row.sort(key=lambda d: d[0])
            sorted_detections.extend(row)

        polygons = []
        for _, _, _, _, c in sorted_detections:
            epsilon = 0.008 * cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, epsilon, True)
            pts = approx.reshape(-1, 2).tolist()
            if len(pts) >= 4:
                polygons.append(pts)
        return JsonResponse({"polygons": polygons, "count": len(polygons)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def auto_classify(request, acq_id):
    if request.method != 'POST':
        return JsonResponse({"error": "POST"}, status=405)
    acq = DataAcquisition.objects.filter(id=acq_id).first()
    if not acq:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    project = acq.project
    if not project or not project.model_cls:
        return JsonResponse({"error": "Modelo de classificação não configurado."}, status=404)
    img = _load_ref_image(acq)
    if img is None:
        return JsonResponse({"error": "imagem não encontrada"}, status=404)
    data = json.loads(request.body or "{}")
    boxes = data.get('boxes', [])
    polygons = data.get('polygons', [])
    try:
        from ultralytics import YOLO
    except ImportError:
        return JsonResponse({"error": "ultralytics não está instalado neste servidor (pip install ultralytics)."}, status=500)
    try:
        model = YOLO(project.model_cls.path)
        img_h, img_w = img.shape[:2]

        def classify_crop(crop):
            if crop.size == 0:
                return ""
            res = model(crop, verbose=False)[0]
            if hasattr(res, "probs") and res.probs is not None:
                return res.names[int(res.probs.top1)]
            return ""

        box_labels = []
        for (x1, y1, x2, y2) in boxes:
            crop = img[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
            box_labels.append(classify_crop(crop))

        poly_labels = []
        for pts in polygons:
            if not pts:
                poly_labels.append(""); continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x1, y1 = max(0, min(xs)), max(0, min(ys))
            x2, y2 = min(img_w, max(xs)), min(img_h, max(ys))
            crop = img[y1:y2, x1:x2]
            poly_labels.append(classify_crop(crop))

        return JsonResponse({"box_labels": box_labels, "poly_labels": poly_labels})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
