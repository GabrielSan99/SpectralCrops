import threading
import time
import os
import glob
import re
import subprocess
import cv2
from PIL import Image


class ArducamCamera:
    _instance = None
    _lock = threading.Lock()

    # A OV9281 USB (UVC) entrega YUYV so em 1280x800/720; MJPG cobre mais
    # resolucoes e taxas. Forcamos MJPG + resolucao pra abertura ser deterministica.
    FOURCC = "MJPG"
    WIDTH = 1280
    HEIGHT = 800
    WARMUP_FRAMES = 5  # descarta os primeiros frames (auto-exposicao estabilizar)

    # Preview do MJPEG (Start Stream). Full-res a qualidade alta gerava
    # ~40KB/frame (~13Mbps a ~40fps) -- em Wi-Fi isso satura e o stream vai
    # acumulando atraso; foi corrigido reduzindo a RESOLUCAO (640x400) na
    # epoca. Agora voltamos pra resolucao cheia (== WIDTH x HEIGHT, a mesma
    # da captura), mas compensando com qualidade JPEG bem mais baixa, pra
    # manter o tamanho por frame parecido (~9-12KB) e nao reintroduzir o
    # travamento. So mexe no tamanho da imagem em si -- se ainda assim
    # atrasar, o proximo ajuste e baixar STREAM_JPEG_QUALITY mais um pouco.
    STREAM_WIDTH = WIDTH
    STREAM_HEIGHT = HEIGHT
    STREAM_JPEG_QUALITY = 35

    # Ganho da camera. 0 = padrao de fabrica (sem ganho extra). Se as fotos
    # ficarem escuras demais no ambiente fechado da caixa, aumente aos poucos
    # (ex.: 20, 40...).
    GAIN = 0

    # Exposicao manual, so pra grab() com `exposure=` -- o stream/preview
    # NUNCA muda de modo, so a captura isolada de uma foto. Unidade = 100us
    # (padrao v4l2/UVC do exposure_time_absolute, confirmado com v4l2-ctl:
    # min=1 max=5000). O auto-exposure normal fica por volta de 150-160
    # (~15-16ms); AUTO_EXPOSURE_TEST testa bem mais longo pra reduzir o
    # brilho de LED necessario (menos estouro/glare nas sementes).
    #
    # CUIDADO: e o proprio MODO de auto_exposure (v4l2 auto_exposure:
    # 3=automatico, 1=manual) que ja travou o streaming dessa camera/driver
    # antes -- por isso so trocamos dentro de grab(), com o device fechado
    # antes/depois (nunca com o stream MJPEG ativo), e sempre revertendo pro
    # automatico logo apos o frame (try/finally), mesmo se algo der errado.
    V4L2_AUTO_EXPOSURE_AUTO = 3    # "Aperture Priority Mode" (automatico)
    V4L2_AUTO_EXPOSURE_MANUAL = 1  # "Manual Mode"

    def __new__(cls, device_index=0):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Abertura preguicosa: nao toca no hardware aqui, so guarda o indice.
            # Assim o app sobe mesmo sem camera conectada; o dispositivo e aberto
            # sob demanda na primeira captura (ver _ensure_open).
            cls._instance.device_index = device_index
            cls._instance.cap = None
        return cls._instance

    NAME_MATCH = "ardu"  # trecho (minusculo) do nome do dispositivo a procurar

    def _candidate_indices(self):
        """Indices de /dev/videoN cujo nome bate com NAME_MATCH (via sysfs,
        nao abre nada ainda). O ultimo indice que funcionou vem primeiro."""
        found = []
        for name_path in sorted(glob.glob('/sys/class/video4linux/video*/name')):
            try:
                with open(name_path) as f:
                    name = f.read().strip()
            except OSError:
                continue
            if self.NAME_MATCH in name.lower():
                m = re.search(r'video(\d+)$', os.path.dirname(name_path))
                if m:
                    found.append(int(m.group(1)))
        # tenta primeiro o que ja funcionou da ultima vez (evita reabrir tudo
        # toda hora quando o indice nao mudou)
        if self.device_index in found:
            found.remove(self.device_index)
            found.insert(0, self.device_index)
        return found or [self.device_index]

    def _open_index(self, idx):
        """Abre e configura um /dev/videoN. Retorna o VideoCapture ou None se
        nao abrir. Igual ao comportamento original: nao invalida a abertura
        por causa dos frames de aquecimento falharem -- isso e tratado depois,
        na leitura real (_capture_frame), nao aqui."""
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.FOURCC))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # sempre o frame mais recente, sem fila acumulando atraso
        if self.GAIN:  # 0 = nao mexe, deixa no padrao de fabrica do driver
            cap.set(cv2.CAP_PROP_GAIN, self.GAIN)
        for _ in range(self.WARMUP_FRAMES):
            cap.read()  # descarta, sem exigir sucesso (igual ao comportamento original)
        return cap

    def _ensure_open(self):
        """Abre a camera sob demanda. Retorna True se estiver disponivel.

        Tenta primeiro o indice atual (comportamento identico ao original).
        So procura outro /dev/videoN pelo NOME se o no atual sumiu de vez
        (ex.: a Arducam reconectou e trocou de indice) -- e nesse caso tenta
        so UM outro indice, com um respiro antes, pra nao martelar dois nos
        da mesma camera fisica em sequencia (isso e o que travava o driver).

        Chamado sempre com ArducamCamera._lock ja adquirido pelo chamador.
        """
        if self.cap is not None and self.cap.isOpened():
            return True

        cap = self._open_index(self.device_index)
        if cap is None:
            for idx in self._candidate_indices():
                if idx == self.device_index:
                    continue
                time.sleep(0.2)  # da um respiro ao USB antes de tentar outro no
                cap = self._open_index(idx)
                if cap is not None:
                    print(f"Camera encontrada em /dev/video{idx} "
                          f"(antes era /dev/video{self.device_index}).")
                    self.device_index = idx
                    break

        if cap is None:
            self.cap = None
            return False
        self.cap = cap
        return True

    def release(self):
        """Libera o device. Uma camera UVC so aceita um processo aberto por vez,
        entao soltar apos a captura evita 'device busy' entre requisicoes."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    # Controles cujo modo automatico precisa ser aplicado ANTES do valor
    # manual dependente (senao o driver rejeita o valor -- o controle fica
    # "inactive" enquanto o automatico correspondente estiver ligado). Como
    # dict preserva ordem de insercao, quem monta `values` so precisa colocar
    # esses dois primeiro (ver views.py: _camera_settings_v4l2_dict).
    AUTO_MODE_CTRLS = ("white_balance_automatic", "auto_exposure")

    def apply_controls(self, values):
        """Aplica controles V4L2/UVC (brilho, ganho, exposicao manual etc.)
        direto no driver via v4l2-ctl -- funciona mesmo com a camera aberta
        por este processo (cv2.VideoCapture): controles nao exigem posse
        exclusiva do device, so o streaming (VIDIOC_STREAMON) exige.
        `values`: dict nome_v4l2 -> valor (bool vira 0/1), NA ORDEM que deve
        ser aplicada. Retorna (ok, mensagem_de_erro)."""
        if not values:
            return True, ""
        ctrl_arg = ",".join(f"{k}={int(v)}" for k, v in values.items())
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", f"/dev/video{self.device_index}", f"--set-ctrl={ctrl_arg}"],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip()
        return True, ""

    def _capture_frame(self):
        """Captura um frame da câmera e retorna o array BGR (ou None)."""
        if not self._ensure_open():
            return None
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def grab(self, exposure=None):
        """Captura e retorna um frame BGR (ou None), liberando o device depois.
        Util pra processar (ex.: detectar QR) alem de/ao inves de salvar.

        Se `exposure` for informado (inteiro, unidade 100us -- ex.: 1000 =
        100ms), troca pra exposicao MANUAL so pra essa foto e devolve pro
        automatico logo em seguida, sempre (try/finally), antes de liberar
        o device. Sem `exposure` (None, o padrao), comportamento identico
        a antes -- fica no automatico o tempo todo."""
        with ArducamCamera._lock:
            if not self._ensure_open():
                return None
            frame = None
            try:
                if exposure is not None:
                    self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.V4L2_AUTO_EXPOSURE_MANUAL)
                    self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
                    for _ in range(2):
                        self.cap.read()  # descarta -- exposicao nova leva 1-2 frames pra valer
                frame = self._capture_frame()
            finally:
                if exposure is not None and self.cap is not None:
                    self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.V4L2_AUTO_EXPOSURE_AUTO)
                self.release()
            return frame

    def capture_stabilized(self, warmup_seconds, exposure=None):
        """Le e DESCARTA frames por `warmup_seconds` (nao um sleep as cegas)
        antes de devolver o ultimo frame lido. E o que da tempo de verdade pro
        auto-exposure/auto-gain da camera convergir -- eles so reagem a
        frames de verdade sendo lidos (igual no stream continuo), nao a um
        sleep() com o device fechado. `grab()` sozinho NAO serve pra isso:
        ele abre, tira UM frame (so ~WARMUP_FRAMES de aquecimento) e fecha --
        se o LED acabou de acender, a exposicao ainda nao convergiu.

        NAO libera o device no final -- quem chama controla isso (release()),
        pra encadear varias capturas (ex.: as 8 bandas) sem pagar reabertura +
        WARMUP_FRAMES do zero a cada banda."""
        with ArducamCamera._lock:
            if not self._ensure_open():
                return None
            if exposure is not None:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.V4L2_AUTO_EXPOSURE_MANUAL)
                self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
            deadline = time.time() + warmup_seconds
            frame = None
            while time.time() < deadline:
                ret, f = self.cap.read()
                if ret:
                    frame = f
            if frame is None:
                frame = self._capture_frame()
            return frame

    # Faixa real do controle exposure_time_absolute (conferida com
    # v4l2-ctl -d /dev/video0 --list-ctrls-menus, mesma da UI). Uma leitura
    # fora dessa faixa e tratada como falha (ver capture_stabilized_auto),
    # nao clampada -- clampar escondia leituras invalidas como se fossem um
    # valor de verdade (era o que fazia toda banda "convergir" pra 5000).
    EXPOSURE_MIN, EXPOSURE_MAX = 1, 5000

    def _get_ctrl(self, name):
        """Le o valor atual de um controle V4L2 direto do driver via
        v4l2-ctl -- mais confiavel que cv2.VideoCapture.get(CAP_PROP_*):
        o mapeamento de propriedades do OpenCV pro V4L2 e generico/as vezes
        incorreto pra controles especificos do driver (foi visto devolvendo
        valor fora de faixa pro exposure_time_absolute sob auto-exposure,
        nunca o valor de verdade que a camera convergiu). apply_controls ja
        usa v4l2-ctl pra ESCREVER; aqui e o mesmo caminho pra LER, por
        simetria e confianca. Retorna int ou None se falhar."""
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", f"/dev/video{self.device_index}", f"--get-ctrl={name}"],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        m = re.search(r':\s*(-?\d+)\s*$', result.stdout.strip())
        return int(m.group(1)) if m else None

    def capture_stabilized_auto(self, warmup_seconds):
        """Igual a capture_stabilized, mas sempre forca exposicao AUTOMATICA e,
        depois da camera convergir, LE de volta o valor que ela escolheu (via
        v4l2-ctl --get-ctrl=exposure_time_absolute -- o driver mantem esse
        controle atualizado com a leitura atual mesmo em modo automatico, so
        nao aceita ESCRITA nele enquanto auto_exposure estiver ligado). Usado
        pra "descobrir" o tempo de exposicao ideal de uma banda e depois
        fixar como manual (ver views.param_band_auto_expose) -- nao serve
        pra captura de medida em si (exposicao automatica varia quadro a
        quadro, nao e reproduzivel).

        `warmup_seconds` pode precisar ser maior que o LED_STABILIZE_SECONDS
        normal: o algoritmo de auto-exposure sobe aos poucos (nao pula direto
        pro valor final), e uma banda escura pode levar mais tempo pra
        convergir de verdade.

        Retorna (frame, exposure) -- exposure e None se a leitura falhar ou
        vier fora da faixa fisica do controle (ver EXPOSURE_MIN/MAX)."""
        with ArducamCamera._lock:
            if not self._ensure_open():
                return None, None
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.V4L2_AUTO_EXPOSURE_AUTO)
            deadline = time.time() + warmup_seconds
            frame = None
            while time.time() < deadline:
                ret, f = self.cap.read()
                if ret:
                    frame = f
            if frame is None:
                frame = self._capture_frame()
            raw_exposure = self._get_ctrl("exposure_time_absolute")
            exposure = (raw_exposure if raw_exposure is not None
                       and self.EXPOSURE_MIN <= raw_exposure <= self.EXPOSURE_MAX else None)
            return frame, exposure

    def save_frame(self, filename, folder=""):
        with ArducamCamera._lock:
            frame = self._capture_frame()
            if frame is None:
                print(f"Erro ao capturar imagem (/dev/video{self.device_index} "
                      f"indisponivel ou sem frame).")
                self.release()
                return

            output_folder = os.path.join("captures_test", folder)
            os.makedirs(output_folder, exist_ok=True)
            path = os.path.join(output_folder, filename)

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            pil_img.save(path)
            print(f"Frame salvo em: {path}")

            # captura discreta: solta o device pra nao travar o proximo acesso
            # (autoreload do runserver, outra requisicao, etc.)
            self.release()

    def stream_frames(self, stop_event=None):
        """Gera MJPEG ate o cliente desconectar ou ``stop_event`` ser acionado.

        O evento permite encerrar explicitamente um stream anterior antes de
        abrir outro. So remover o ``src`` no navegador nao fecha a conexao
        HTTP de imediato em todos os browsers.
        """
        try:
            while stop_event is None or not stop_event.is_set():
                with ArducamCamera._lock:
                    frame = self._capture_frame()

                if stop_event is not None and stop_event.is_set():
                    break
                if frame is not None:
                    # so redimensiona se o preview for menor que a captura --
                    # atualmente sao iguais (resolucao cheia), entao redimensionar seria a-toa
                    if (self.STREAM_WIDTH, self.STREAM_HEIGHT) != (frame.shape[1], frame.shape[0]):
                        frame = cv2.resize(frame, (self.STREAM_WIDTH, self.STREAM_HEIGHT),
                                            interpolation=cv2.INTER_AREA)
                    ret, jpeg = cv2.imencode('.jpg', frame,
                                              [cv2.IMWRITE_JPEG_QUALITY, self.STREAM_JPEG_QUALITY])
                    if ret:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

                time.sleep(0.01)
        finally:
            # ao encerrar o stream, libera a camera pros outros acessos
            with ArducamCamera._lock:
                self.release()
