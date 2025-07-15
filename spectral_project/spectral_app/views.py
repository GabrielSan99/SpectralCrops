from django.shortcuts import render
from django.contrib.auth.decorators import login_required

import pigpio
from mvIMPACT import acquire
import ctypes
import numpy as np
from PIL import Image
import os
from datetime import datetime


# Create your views here.
# MARK: HOME
@login_required
def index(request):
    return render(
            request,
            "pages/index.html",
            {

            },
        )



@login_required
def tests(request):
    if request.method == 'POST':

        action = request.POST.get('action')

        pi = pigpio.pi() # Connect to pigpio daemon

        WHITE_LED = 17
        RED_LED = 27
        YELLOW_LED = 22
        BLUE_LED = 23
        
        
        


        if action == 'white_led':
            print("Turn on white led!")
            pi.write(WHITE_LED, 1) 
        
        elif action == 'yellow_led':
            print("Turn on yellow led!")
            pi.write(YELLOW_LED, 1)

        elif action == 'red_led':
            print("Turn on red led!")
            pi.write(YELLOW_LED, 1)

        elif action == 'blue_led':
            print("Turn on blue led!")
            pi.write(BLUE_LED, 1)

        elif action == 'turn_off':
            print("Turn off all leds!")
            pi.write(WHITE_LED, 0)
            pi.write(YELLOW_LED, 0)
            pi.write(RED_LED, 0)
            pi.write(BLUE_LED, 0)

        elif action == 'get_frame':
            print("Saved frame!")
            
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
                
                output_folder = "captures_test"
                os.makedirs(output_folder, exist_ok=True)

                now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                filename = os.path.join(output_folder, f"capture_{now}.png")

                if channels == 1:
                    pil_img = Image.fromarray(frame, mode='I;16' if bitDepth > 8 else 'L')
                else:
                    pil_img = Image.fromarray(frame, mode='RGB')

                pil_img.save(filename)
                print(f"Frame salvo como: {filename}")

            # Libera recursos
            pRequest.unlock()
            if pDev.acquisitionStartStopBehaviour.read() == ASSB_USER:
                fi.acquisitionStop() 

    return render(request, "pages/tests.html",
                    { 

                    })
