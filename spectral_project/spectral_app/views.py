from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.files.base import ContentFile
from django.http import JsonResponse


import pigpio
from .camera_functions import *
import os
from datetime import datetime
import base64
import time

camera = ArducamCamera()

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
def video_feed(request):
    return StreamingHttpResponse(camera.stream_frames(),
                                 content_type='multipart/x-mixed-replace; boundary=frame')

@csrf_exempt
def img_segmentation(request):
    if request.method == "POST":
        data_url = request.POST.get("imagem")
        format, imgstr = data_url.split(';base64,') 
        ext = format.split('/')[-1] 
        data = ContentFile(base64.b64decode(imgstr), name='sample_selected.' + ext)
        
        # Aqui você poderia salvar no modelo, ou apenas retornar OK
        # Exemplo: salvar em MEDIA_ROOT
        from django.conf import settings
        import os
        path = os.path.join(settings.MEDIA_ROOT, data.name)
        with open(path, 'wb') as f:
            f.write(data.read())

        return JsonResponse({"status": "ok", "url": f"/media/{data.name}"})
    return JsonResponse({"status": "erro"})


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
            pi.write(RED_LED, 1)

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

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            camera.save_frame(filename=f"capture_{now}.png")
        
        elif action == 'get_all_bands':
            print("Starting capture all bands...")

            pi.write(RED_LED, 0)
            pi.write(YELLOW_LED, 0)
            pi.write(BLUE_LED, 0)
            pi.write(WHITE_LED, 0)

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            print(1111)
            pi.write(RED_LED, 1)
            camera.save_frame(filename=f"red_{now}.png", folder=f"captures_{now}")
            pi.write(RED_LED, 0)
            print(2222)
            pi.write(YELLOW_LED, 1)
            camera.save_frame(filename=f"yellow_{now}.png", folder=f"captures_{now}")
            pi.write(YELLOW_LED, 0)

            pi.write(BLUE_LED, 1)
            camera.save_frame(filename=f"blue_{now}.png", folder=f"captures_{now}")
            pi.write(BLUE_LED, 0)

            pi.write(WHITE_LED, 1)
            camera.save_frame(filename=f"white_{now}.png", folder=f"captures_{now}")
            pi.write(WHITE_LED, 0)

        return redirect('tests')

    return render(request, "pages/tests.html",
                    { 

                    })