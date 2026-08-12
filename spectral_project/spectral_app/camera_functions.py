import threading
import time
import os
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

    def __new__(cls, device_index=0):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Abertura preguicosa: nao toca no hardware aqui, so guarda o indice.
            # Assim o app sobe mesmo sem camera conectada; o dispositivo e aberto
            # sob demanda na primeira captura (ver _ensure_open).
            cls._instance.device_index = device_index
            cls._instance.cap = None
        return cls._instance

    def _ensure_open(self):
        """Abre a camera sob demanda. Retorna True se estiver disponivel.

        Chamado sempre com ArducamCamera._lock ja adquirido pelo chamador.
        """
        if self.cap is not None and self.cap.isOpened():
            return True
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            self.cap = None
            return False
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.FOURCC))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
        # descarta alguns frames pra exposicao/ganho estabilizarem (so na abertura)
        for _ in range(self.WARMUP_FRAMES):
            cap.read()
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

    def stream_frames(self):
        try:
            while True:
                with ArducamCamera._lock:
                    frame = self._capture_frame()

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
