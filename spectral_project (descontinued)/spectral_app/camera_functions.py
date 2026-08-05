import threading
import time
import os
import ctypes
import numpy as np
import cv2
from PIL import Image
from mvIMPACT import acquire

class BlueFoxCamera:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_camera()
        return cls._instance

    def _init_camera(self):
        self.devMgr = acquire.DeviceManager()
        if self.devMgr.deviceCount() == 0:
            raise Exception("Nenhuma câmera encontrada.")

        self.pDev = self.devMgr[0]
        self.pDev.open()
        self.fi = acquire.FunctionInterface(self.pDev)

        # Enfileira alguns buffers
        for _ in range(5):
            self.fi.imageRequestSingle()

        ASSB_USER = 0
        if self.pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
            self.fi.acquisitionStart()

    def _capture_request(self):
        """Solicita um frame da câmera e retorna request."""
        requestNr = self.fi.imageRequestWaitFor(5000)
        if not self.fi.isRequestNrValid(requestNr):
            return None
        return self.fi.getRequest(requestNr)

    def save_frame(self, filename, folder=""):
        with BlueFoxCamera._lock:
            pRequest = self._capture_request()
            if not pRequest or not pRequest.isOK:
                print("Erro ao capturar imagem.")
                return

            width = pRequest.imageWidth.read()
            height = pRequest.imageHeight.read()
            channels = pRequest.imageChannelCount.read()
            bitDepth = pRequest.imageChannelBitDepth.read()
            bufferPtr = pRequest.imageData.read()
            bufferSize = pRequest.imageSize.read()

            cbuf = (ctypes.c_char * bufferSize).from_address(int(bufferPtr))
            dtype = np.uint16 if bitDepth > 8 else np.uint8
            img_array = np.frombuffer(cbuf, dtype=dtype)

            if channels == 1:
                frame = img_array.reshape((height, width))
            else:
                frame = img_array.reshape((height, width, channels))

            output_folder = os.path.join("captures_test", folder)
            os.makedirs(output_folder, exist_ok=True) 
            path = os.path.join(output_folder, filename)

            if channels == 1:
                pil_img = Image.fromarray(frame, mode='I;16' if bitDepth > 8 else 'L')
            else:
                pil_img = Image.fromarray(frame, mode='RGB')

            pil_img.save(path)
            print(f"Frame salvo em: {path}")

            pRequest.unlock()
            self.fi.imageRequestSingle()

    def stream_frames(self):
        pPreviousRequest = None
        try:
            while True:
                with BlueFoxCamera._lock:
                    pRequest = self._capture_request()
                    if pRequest and pRequest.isOK:
                        width = pRequest.imageWidth.read()
                        height = pRequest.imageHeight.read()
                        channels = pRequest.imageChannelCount.read()
                        bitDepth = pRequest.imageChannelBitDepth.read()
                        bufferPtr = pRequest.imageData.read()
                        bufferSize = pRequest.imageSize.read()

                        cbuf = (ctypes.c_char * bufferSize).from_address(int(bufferPtr))
                        dtype = np.uint16 if bitDepth > 8 else np.uint8
                        img_array = np.frombuffer(cbuf, dtype=dtype)

                        if channels == 1:
                            frame = img_array.reshape((height, width))
                        else:
                            frame = img_array.reshape((height, width, channels))

                        if bitDepth > 8:
                            display_frame = cv2.convertScaleAbs(frame, alpha=(255.0/65535.0))
                        else:
                            display_frame = frame

                        ret, jpeg = cv2.imencode('.jpg', display_frame)
                        if ret:
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

                    if pPreviousRequest:
                        pPreviousRequest.unlock()
                    pPreviousRequest = pRequest
                    self.fi.imageRequestSingle()

                time.sleep(0.01)

        finally:
            if pPreviousRequest:
                pPreviousRequest.unlock()
