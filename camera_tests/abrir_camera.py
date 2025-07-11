from mvIMPACT import acquire
import platform
import time

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

# Inicia aquisição manual se necessário
if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
    fi.acquisitionStart()

print("Capturando frames... Pressione Ctrl+C para parar")
pPreviousRequest = None

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
