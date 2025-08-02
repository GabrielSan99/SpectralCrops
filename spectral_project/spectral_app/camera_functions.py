from mvIMPACT import acquire
import numpy as np
import ctypes
from PIL import Image
import os
import time
import cv2


class BlueFoxCamera:
    # def __init__(self):
        # self.devMgr = acquire.DeviceManager()
        # if self.devMgr.deviceCount() == 0:
        #     raise Exception("Nenhuma câmera encontrada.")
        # self.pDev = self.devMgr[0]

    def save_frame(self, filename, folder=""):
        # Inicializa o gerenciador de dispositivos
        devMgr = acquire.DeviceManager()
        if devMgr.deviceCount() == 0:
            print("Nenhuma câmera encontrada.")
            exit()

        # Seleciona e abre a primeira câmera
        pDev = devMgr[0]
        pDev.open()

        fi = acquire.FunctionInterface(pDev)

        # Enfileira buffers
        for _ in range(5):
            fi.imageRequestSingle()

        # Inicia aquisição se necessário
        ASSB_USER = 0
        if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
            fi.acquisitionStart()

        # Espera por uma imagem
        requestNr = fi.imageRequestWaitFor(5000)
        if not fi.isRequestNrValid(requestNr):
            print("Timeout esperando imagem.")
            exit()

        pRequest = fi.getRequest(requestNr)
        if not pRequest.isOK:
            print("Erro ao capturar imagem.")
            exit()

        # Dados da imagem
        width = pRequest.imageWidth.read()
        height = pRequest.imageHeight.read()
        channels = pRequest.imageChannelCount.read()
        bitDepth = pRequest.imageChannelBitDepth.read()
        bufferPtr = pRequest.imageData.read()
        bufferSize = pRequest.imageSize.read()

        # Criação do numpy array
        cbuf = (ctypes.c_char * bufferSize).from_address(int(bufferPtr))
        dtype = np.uint16 if bitDepth > 8 else np.uint8
        img_array = np.frombuffer(cbuf, dtype=dtype)

        if channels == 1:
            frame = img_array.reshape((height, width))
        elif channels == 3:
            frame = img_array.reshape((height, width, 3))
        else:
            print(f"Número de canais inesperado: {channels}")
            frame = None

        # Salvar imagem
        if frame is not None:
            
            output_folder = "captures_test/" + folder
            os.makedirs(output_folder, exist_ok=True) 

            path = os.path.join(output_folder, filename)

            if channels == 1:
                pil_img = Image.fromarray(frame, mode='I;16' if bitDepth > 8 else 'L')
            else:
                pil_img = Image.fromarray(frame, mode='RGB')

            pil_img.save(path)
            print(f"Frame salvo como: {path}")

        # Libera recursos
        pRequest.unlock()
        if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
            fi.acquisitionStop()

    def stream_frames(self):
        devMgr = acquire.DeviceManager()
        if devMgr.deviceCount() == 0:
            raise Exception("Nenhuma câmera encontrada.")

        pDev = devMgr[0]
        pDev.open()
        fi = acquire.FunctionInterface(pDev)

        # Enfileira buffers
        for _ in range(5):
            fi.imageRequestSingle()

        ASSB_USER = 0
        if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
            fi.acquisitionStart()

        pPreviousRequest = None

        try:
            while True:
                requestNr = fi.imageRequestWaitFor(5000)
                if fi.isRequestNrValid(requestNr):
                    pRequest = fi.getRequest(requestNr)
                    if pRequest.isOK:
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

                        # Ajusta a imagem para 8-bit se for maior (por exemplo, 16 bits)
                        if bitDepth > 8:
                            display_frame = cv2.convertScaleAbs(frame, alpha=(255.0/65535.0))
                        else:
                            display_frame = frame

                        # Codifica para JPEG para o streaming
                        ret, jpeg = cv2.imencode('.jpg', display_frame)
                        if ret:
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

                    # Libera o buffer da imagem anterior para não travar o stream
                    if pPreviousRequest:
                        pPreviousRequest.unlock()
                    pPreviousRequest = pRequest
                    fi.imageRequestSingle()
                else:
                    time.sleep(0.01)

        finally:
            if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
                fi.acquisitionStop()
            if pPreviousRequest:
                pPreviousRequest.unlock()