import threading
import time
import os
import glob
import re
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

    # Ganho da camera. 0 = padrao de fabrica (sem ganho extra). Se as fotos
    # ficarem escuras demais no ambiente fechado da caixa, aumente aos poucos
    # (ex.: 20, 40...). Nao mude o modo de auto_exposure: trocar trava o
    # streaming nessa camera/driver.
    GAIN = 0

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

    def _capture_frame(self):
        """Captura um frame da câmera e retorna o array BGR (ou None)."""
        if not self._ensure_open():
            return None
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def grab(self):
        """Captura e retorna um frame BGR (ou None), liberando o device depois.
        Util pra processar (ex.: detectar QR) alem de/ao inves de salvar."""
        with ArducamCamera._lock:
            frame = self._capture_frame()
            self.release()
            return frame

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
                    ret, jpeg = cv2.imencode('.jpg', frame)
                    if ret:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

                time.sleep(0.01)
        finally:
            # ao encerrar o stream, libera a camera pros outros acessos
            with ArducamCamera._lock:
                self.release()
