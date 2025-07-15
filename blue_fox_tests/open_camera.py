from mvIMPACT import acquire
import time
import ctypes
import numpy as np
import cv2

# Inicializa o gerenciador de dispositivos
devMgr = acquire.DeviceManager()
if devMgr.deviceCount() == 0:
    print("Nenhuma câmera encontrada.")
    exit()

# Seleciona e abre a primeira câmera
pDev = devMgr[0]
pDev.open()

# Inicializa funções de aquisição
fi = acquire.FunctionInterface(pDev)
stats = acquire.Statistics(pDev)

# Enfileira buffers
for _ in range(5):
    fi.imageRequestSingle()

# Inicia aquisição, se necessário
ASSB_USER = 0
if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
    fi.acquisitionStart()

print("Capturando frames... Pressione 'q' para sair")

pPreviousRequest = None

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

                # Converte buffer para numpy array
                cbuf = (ctypes.c_char * bufferSize).from_address(int(bufferPtr))
                dtype = np.uint16 if bitDepth > 8 else np.uint8
                img_array = np.frombuffer(cbuf, dtype=dtype)

                print("Width: ", width)
                print("Height: ", height)
                print("Channels:", channels)
                print("BitDepth: ", bitDepth)
                print("BufferPtr: ", bufferPtr)
                print("BufferSize: ", bufferSize)

                # Redimensiona imagem
                if channels == 1:
                    frame = img_array.reshape((height, width))
                else:
                    frame = img_array.reshape((height, width, channels))

                # Exibe imagem normalizada (para visualização de 16 bits)
                if bitDepth > 8:
                    display_frame = cv2.convertScaleAbs(frame, alpha=(255.0/65535.0))
                else:
                    display_frame = frame

                cv2.imshow("Stream da BlueFOX", display_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("Encerrando pela tecla 'q'")
                    break

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
