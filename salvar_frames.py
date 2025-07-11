from mvIMPACT import acquire
import platform
import time
import ctypes
import numpy as np
from PIL import Image
import os
from datetime import datetime

# Inicializa o gerenciador de dispositivos
devMgr = acquire.DeviceManager()
if devMgr.deviceCount() == 0:
    print("Nenhuma câmera encontrada.")
    exit()

# Seleciona a primeira câmera (índice 0)
pDev = devMgr[0]
pDev.open()

fi = acquire.FunctionInterface(pDev)
stats = acquire.Statistics(pDev)

isWindows = platform.system() == "Windows"
if isWindows:
    display = acquire.ImageDisplayWindow("Stream da BlueFOX")

# Envia alguns buffers para fila
for _ in range(5):
    fi.imageRequestSingle()

# Valor fixo correspondente ao modo 'assbUser'
ASSB_USER = 0
if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
    fi.acquisitionStart()

print("Capturando frames... Pressione Ctrl+C para parar")
pPreviousRequest = None
frame_count = 0


# Cria a pasta 'frames' se ainda não existir
now = datetime.now().strftime("frames_%Y-%m-%d_%H-%M-%S")
output_folder = "frames_" + now 
os.makedirs(output_folder, exist_ok=True)

try:
    while True:
        requestNr = fi.imageRequestWaitFor(5000)
        if fi.isRequestNrValid(requestNr):
            pRequest = fi.getRequest(requestNr)
            if pRequest.isOK:
                print(f"Frame: {stats.frameCount.readS()} | FPS: {stats.framesPerSecond.readS()}")
                if isWindows:
                    display.GetImageDisplay().SetImage(pRequest)
                    display.GetImageDisplay().Update()

                # --- PEGAR BUFFER E SALVAR COMO IMAGEM ---
                w = pRequest.imageWidth.read()
                h = pRequest.imageHeight.read()
                c = pRequest.imageChannelCount.read()
                bpp = 1 if pRequest.imageChannelBitDepth.read() <= 8 else 2  # bytes por pixel por canal

                size = w * h * c * bpp
                ptr = pRequest.imageData.read()  # ponteiro

                # Cria buffer do tipo correto a partir do ponteiro
                buf_type = ctypes.c_ubyte * size
                buf = buf_type.from_address(int(ptr))

                # Converte para NumPy
                arr = np.frombuffer(buf, dtype=np.uint8)
                if c == 1:
                    arr = arr.reshape((h, w))
                    img = Image.fromarray(arr, mode='L')
                elif c == 3:
                    arr = arr.reshape((h, w, 3))
                    img = Image.fromarray(arr, mode='RGB')
                else:
                    print(f"Canal inesperado: {c}, salvando ignorado.")
                    img = None

                # Salva a imagem
                if img:
                    # Dentro do loop, substitua a linha de save:
                    img.save(os.path.join(output_folder, f"frame_{frame_count:04d}.png"))
                    print(f"Salvo: frame_{frame_count:04d}.png")
                    frame_count += 1
                # --- FIM SALVAMENTO ---

            else:
                print("Erro ao capturar imagem.")
            if pPreviousRequest:
                pPreviousRequest.unlock()
            pPreviousRequest = pRequest
            fi.imageRequestSingle()
        else:
            print("Timeout ao esperar imagem.")
        time.sleep(0.01)

except KeyboardInterrupt:
    print("Encerrando...")

finally:
    if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
        fi.acquisitionStop()
    if pPreviousRequest:
        pPreviousRequest.unlock()
