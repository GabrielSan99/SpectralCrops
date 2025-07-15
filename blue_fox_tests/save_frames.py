from mvIMPACT import acquire
import time
import ctypes
import numpy as np
import cv2
from PIL import Image
import os
from datetime import datetime

# Inicializa o gerenciador de dispositivos
devMgr = acquire.DeviceManager()
if devMgr.deviceCount() == 0:
    print("Nenhuma câmera encontrada.")
    exit()

# Seleciona e abre a primeira câmera
pDev = devMgr[0]
pDev.open()

# Interfaces de aquisição e estatísticas
fi = acquire.FunctionInterface(pDev)
stats = acquire.Statistics(pDev)

# Enfileira buffers
for _ in range(5):
    fi.imageRequestSingle()

# Inicia aquisição, se necessário
ASSB_USER = 0
if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
    fi.acquisitionStart()

# Cria pasta para salvar os frames
now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
output_folder = f"frames_{now}"
os.makedirs(output_folder, exist_ok=True)

print("Capturando frames... Pressione 'q' ou Ctrl+C para sair")

pPreviousRequest = None
frame_count = 0

try:
    while True:
        requestNr = fi.imageRequestWaitFor(5000)
        if fi.isRequestNrValid(requestNr):
            pRequest = fi.getRequest(requestNr)
            if pRequest.isOK:
                # Informações da imagem
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
                else:
                    frame = img_array.reshape((height, width, channels))

                # Mostra imagem normalizada (caso seja 16 bits)
                if bitDepth > 8:
                    display_frame = cv2.convertScaleAbs(frame, alpha=(255.0/65535.0))
                else:
                    display_frame = frame

                # Exibe com OpenCV
                cv2.imshow("Stream da BlueFOX", display_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("Encerrando pela tecla 'q'")
                    break

                # --- Salvando o frame como imagem ---
                filename = os.path.join(output_folder, f"frame_{frame_count:04d}.png")
                if channels == 1:
                    pil_img = Image.fromarray(frame, mode='I;16' if bitDepth > 8 else 'L')
                elif channels == 3:
                    pil_img = Image.fromarray(frame, mode='RGB')
                else:
                    print(f"Canal inesperado: {channels}, frame não salvo.")
                    pil_img = None

                if pil_img:
                    pil_img.save(filename)
                    print(f"Salvo: {filename}")
                    frame_count += 1

                print(f"Frame: {stats.frameCount.readS()} | FPS: {stats.framesPerSecond.readS()}")

            else:
                print("Erro ao capturar imagem.")

            if pPreviousRequest:
                pPreviousRequest.unlock()
            pPreviousRequest = pRequest
            fi.imageRequestSingle()
        else:
            print("Timeout esperando imagem.")
        time.sleep(0.01)

except KeyboardInterrupt:
    print("Interrompido por Ctrl+C")

finally:
    if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
        fi.acquisitionStop()
    if pPreviousRequest:
        pPreviousRequest.unlock()
    cv2.destroyAllWindows()
