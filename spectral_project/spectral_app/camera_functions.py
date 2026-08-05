import threading
import time
import os
import cv2
from PIL import Image

class ArducamCamera:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, device_index=0):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_camera(device_index)
        return cls._instance

    def _init_camera(self, device_index):
        self.cap = cv2.VideoCapture(device_index)
        if not self.cap.isOpened():
            raise Exception("Nenhuma câmera encontrada.")

    def _capture_frame(self):
        """Captura um frame da câmera e retorna o array BGR."""
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def save_frame(self, filename, folder=""):
        with ArducamCamera._lock:
            frame = self._capture_frame()
            if frame is None:
                print("Erro ao capturar imagem.")
                return

            output_folder = os.path.join("captures_test", folder)
            os.makedirs(output_folder, exist_ok=True)
            path = os.path.join(output_folder, filename)

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            pil_img.save(path)
            print(f"Frame salvo em: {path}")

    def stream_frames(self):
        while True:
            with ArducamCamera._lock:
                frame = self._capture_frame()

            if frame is not None:
                ret, jpeg = cv2.imencode('.jpg', frame)
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

            time.sleep(0.01)
