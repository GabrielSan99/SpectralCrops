#!/usr/bin/env python3
# Porta do motor_web_esp32.cpp para Raspberry Pi (Flask + RPi.GPIO).
# Pinos: DIR=GPIO6 (do esquematico), STEP=GPIO13, EN=GPIO5, Limit switch=GPIO26.

import time
import RPi.GPIO as GPIO
from flask import Flask, request, jsonify

# ── Pinos ─────────────────────────────────────────────
# EN precisa comecar em HIGH (driver desabilitado) desde o power-on, antes
# do script rodar - senao o motor fica acionado sozinho assim que liga na
# tomada. O Pi tem pull-up interno de fabrica so no range GPIO0-GPIO8
# (fica HIGH por padrao); GPIO9 em diante tem pull-down de fabrica (ficaria
# LOW = motor acionado). Por isso o EN foi colocado no GPIO5, que era do
# STEP - e o STEP foi realocado pro GPIO13 (fora do range 0-8, mas sem
# problema: STEP so pulsa quando o script chama girar(), um pulso perdido
# no boot antes disso e inofensivo, diferente do EN ficar preso em LOW).
STEP_PIN  = 13
DIR_PIN   = 6    # do esquematico, sem necessidade de estar no range 0-8
EN_PIN    = 5    # LOW = habilitado | HIGH = bobinas desligadas
LIMIT_PIN = 26   # switch de fim de curso (nao lido ainda, ver handle_status)

# ── Configuracoes do motor ───────────────────────────────
STEPS_PER_REV    = 200   # NEMA 17 full step = 200 passos/volta
DEGREES_PER_MOVE = 18    # graus por clique nos botoes

# ── Estado ────────────────────────────────────────────────
posicao_graus = 0.0

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(EN_PIN, GPIO.OUT)
GPIO.setup(LIMIT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.output(STEP_PIN, GPIO.LOW)
GPIO.output(DIR_PIN, GPIO.LOW)
GPIO.output(EN_PIN, GPIO.HIGH)  # comeca desligado

app = Flask(__name__)


# ── Funcao do motor (igual ao test_nema.cpp) ─────────────

def girar(horario, passos):
    global posicao_graus
    GPIO.output(DIR_PIN, GPIO.HIGH if horario else GPIO.LOW)
    GPIO.output(EN_PIN, GPIO.LOW)  # habilita antes de girar

    steps_done = 0
    try:
        for _ in range(passos):
            GPIO.output(STEP_PIN, GPIO.HIGH)
            time.sleep(0.001)

            GPIO.output(STEP_PIN, GPIO.LOW)
            time.sleep(0.001)

            steps_done += 1
    finally:
        # sempre desabilita, mesmo se der erro/excecao/interrupcao no meio do giro,
        # senao o motor fica energizado e pode superaquecer
        GPIO.output(EN_PIN, GPIO.HIGH)

        delta = steps_done / STEPS_PER_REV * 360.0
        posicao_graus += delta if horario else -delta


# ── HTML da interface (mesma do motor_web_esp32.cpp) ──────

INDEX_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Motor Raspberry Pi</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #0f1117;
      color: #e0e0e0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      gap: 2rem;
    }

    h1 {
      font-size: 1.4rem;
      font-weight: 400;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: #888;
    }

    .card {
      background: #1a1d27;
      border: 1px solid #2a2d3a;
      border-radius: 16px;
      padding: 2.5rem 3rem;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2rem;
      width: 340px;
    }

    .dial-wrap { position: relative; width: 160px; height: 160px; }
    #dial-svg  { width: 160px; height: 160px; transform: rotate(-90deg); }
    .dial-track { fill: none; stroke: #2a2d3a; stroke-width: 8; }
    .dial-arc   { fill: none; stroke: #4f8ef7; stroke-width: 8;
                  stroke-linecap: round; transition: stroke-dashoffset 0.35s ease; }
    .dial-label {
      position: absolute; inset: 0;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
    }
    .dial-label span:first-child { font-size: 2rem; font-weight: 600;
                                   color: #fff; line-height: 1; }
    .dial-label span:last-child  { font-size: 0.75rem; color: #555;
                                   letter-spacing: 0.1em; }

    .buttons { display: flex; gap: 1.2rem; }

    button {
      width: 110px; height: 110px;
      border-radius: 50%;
      border: 2px solid #2a2d3a;
      background: #12151e;
      color: #e0e0e0;
      font-size: 0.8rem; font-weight: 500; letter-spacing: 0.05em;
      cursor: pointer;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 6px;
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
      user-select: none;
    }
    button:hover   { background: #1e2235; border-color: #4f8ef7; }
    button:active  { transform: scale(0.95); }
    button:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
    button svg { width: 28px; height: 28px; stroke: #4f8ef7; fill: none;
                 stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }

    .limit-section {
      display: flex; flex-direction: column;
      align-items: center; gap: 10px; width: 100%;
    }
    .limit-label {
      font-size: 0.72rem; letter-spacing: 0.1em;
      text-transform: uppercase; color: #555;
    }
    .led-wrap { display: flex; align-items: center; gap: 12px; }

    #led-svg .led-body  { fill: #1a0000; transition: fill 0.2s; }
    #led-svg .led-glow  { fill: none; opacity: 0; transition: opacity 0.2s; }
    #led-svg .led-shine { fill: rgba(255,255,255,0.15); }
    #led-svg .led-base  { fill: #2a2d3a; }
    #led-svg .led-legs  { stroke: #444; fill: none; stroke-width: 1.5; }
    #led-svg.on .led-body { fill: #ff2222;
                            filter: drop-shadow(0 0 6px #ff0000); }
    #led-svg.on .led-glow { opacity: 1; }

    .led-status {
      font-size: 0.85rem; font-weight: 600;
      color: #333; transition: color 0.2s; letter-spacing: 0.04em;
    }
    .led-status.on { color: #ff4444; }

    #btn-reset {
      width: auto; height: auto;
      border-radius: 8px; padding: 8px 20px;
      font-size: 0.75rem; color: #555; border-color: #222;
    }
    #btn-reset:hover { color: #e0e0e0; border-color: #4f8ef7;
                       background: #1e2235; }

    #log { font-size: 0.72rem; color: #444;
           min-height: 1.2em; letter-spacing: 0.04em; }
  </style>
</head>
<body>

<h1>Motor de Passo — Raspberry Pi</h1>

<div class="card">

  <div class="dial-wrap">
    <svg id="dial-svg" viewBox="0 0 160 160">
      <circle class="dial-track" cx="80" cy="80" r="66"/>
      <circle class="dial-arc" cx="80" cy="80" r="66"
              id="arc" stroke-dasharray="414.69" stroke-dashoffset="414.69"/>
    </svg>
    <div class="dial-label">
      <span id="pos-deg">0.0</span>
      <span>graus</span>
    </div>
  </div>

  <div class="buttons">
    <button id="btn-ccw" onclick="move('ccw')">
      <svg viewBox="0 0 24 24">
        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
        <path d="M3 3v5h5"/>
      </svg>
      Anti-horário
    </button>
    <button id="btn-cw" onclick="move('cw')">
      <svg viewBox="0 0 24 24">
        <path d="M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
        <path d="M21 3v5h-5"/>
      </svg>
      Horário
    </button>
  </div>

  <div class="limit-section">
    <span class="limit-label">Limit Switch</span>
    <div class="led-wrap">
      <svg id="led-svg" width="40" height="64" viewBox="0 0 40 64">
        <g class="led-legs">
          <line x1="14" y1="52" x2="10" y2="64"/>
          <line x1="26" y1="52" x2="30" y2="64"/>
        </g>
        <rect class="led-base" x="8" y="44" width="24" height="10" rx="3"/>
        <ellipse class="led-body" cx="20" cy="30" rx="17" ry="17"/>
        <ellipse class="led-glow" cx="20" cy="30" rx="17" ry="17"
                 style="fill:#ff4444; filter:blur(4px);"/>
        <ellipse class="led-shine" cx="14" cy="22" rx="5" ry="3"
                 transform="rotate(-30 14 22)"/>
      </svg>
      <span class="led-status" id="led-status">Livre</span>
    </div>
  </div>

  <p id="log">Pronto.</p>
  <button id="btn-reset" onclick="resetPos()">Zerar posição</button>

</div>

<script>
  const ARC_CIRC = 414.69;
  let busy = false;

  function pollStatus() {
    fetch('/status')
      .then(r => r.json())
      .then(d => { updateDial(d.pos); updateLed(d.limit); })
      .catch(() => {})
      .finally(() => setTimeout(pollStatus, 500));
  }
  pollStatus();

  function setLog(msg) { document.getElementById('log').textContent = msg; }

  function setBusy(state) {
    busy = state;
    document.getElementById('btn-cw').disabled  = state;
    document.getElementById('btn-ccw').disabled = state;
  }

  function updateDial(deg) {
    document.getElementById('pos-deg').textContent = parseFloat(deg).toFixed(1);
    const norm   = ((deg % 360) + 360) % 360;
    const offset = ARC_CIRC - (norm / 360) * ARC_CIRC;
    document.getElementById('arc').style.strokeDashoffset = offset;
  }

  function updateLed(triggered) {
    const svg    = document.getElementById('led-svg');
    const status = document.getElementById('led-status');
    if (triggered) {
      svg.classList.add('on');
      status.classList.add('on');
      status.textContent = 'Acionado!';
    } else {
      svg.classList.remove('on');
      status.classList.remove('on');
      status.textContent = 'Livre';
    }
  }

  function move(dir) {
    if (busy) return;
    setBusy(true);
    setLog(dir === 'cw' ? '↻ Movendo horário...' : '↺ Movendo anti-horário...');

    fetch('/move?dir=' + dir)
      .then(r => r.json())
      .then(d => {
        updateDial(d.pos);
        updateLed(d.limit);
        if (d.limit) {
          setLog('⚠ Limit switch acionado! Posição home.');
        } else {
          setLog('Movido ' + (dir === 'cw' ? 'horário' : 'anti-horário') +
                 '  —  posição: ' + parseFloat(d.pos).toFixed(1) + '°');
        }
      })
      .catch(() => setLog('Erro na comunicação.'))
      .finally(() => setBusy(false));
  }

  function resetPos() {
    fetch('/reset')
      .then(r => r.json())
      .then(() => { updateDial(0); setLog('Posição zerada.'); });
  }
</script>
</body>
</html>
"""


# ── Rotas do servidor ───────────────────────────────────

@app.route("/")
def handle_root():
    return INDEX_HTML


@app.route("/move")
def handle_move():
    dir_arg = request.args.get("dir", "")
    horario = (dir_arg == "cw")

    passos = round(DEGREES_PER_MOVE * STEPS_PER_REV / 360.0)
    girar(horario, passos)

    # ativo em LOW pelo PUD_UP (switch fecha o pino no GND)
    limit = (GPIO.input(LIMIT_PIN) == GPIO.LOW)

    return jsonify(pos=round(posicao_graus, 1), limit=limit)


@app.route("/status")
def handle_status():
    limit = (GPIO.input(LIMIT_PIN) == GPIO.LOW)
    return jsonify(pos=round(posicao_graus, 1), limit=limit)


@app.route("/reset")
def handle_reset():
    global posicao_graus
    posicao_graus = 0.0
    return jsonify(pos=0.0)


if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=8080)
    finally:
        GPIO.output(EN_PIN, GPIO.HIGH)  # desabilita antes de qualquer coisa
        # cleanup() so nos pinos que podem ficar flutuando sem problema.
        # NAO limpa o EN_PIN: cleanup() o reconfiguraria como input flutuante,
        # e o driver interpreta o pino solto como LOW = motor acionado.
        GPIO.cleanup([STEP_PIN, DIR_PIN, LIMIT_PIN])
