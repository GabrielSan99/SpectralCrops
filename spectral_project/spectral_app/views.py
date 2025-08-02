from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse

import pigpio
from mvIMPACT import acquire
from .camera_functions import *
import ctypes
import numpy as np
from PIL import Image
import os
from datetime import datetime
import time


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
    camera = BlueFoxCamera()
    return StreamingHttpResponse(camera.stream_frames(),
                                 content_type='multipart/x-mixed-replace; boundary=frame')

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

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            camera = BlueFoxCamera()
            camera.save_frame(filename=f"capture_{now}.png")
        
        elif action == 'get_all_bands':
            print("Starting capture all bands...")

            pi.write(RED_LED, 0)
            pi.write(YELLOW_LED, 0)
            pi.write(BLUE_LED, 0)
            pi.write(WHITE_LED, 0)

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            camera = BlueFoxCamera()

            pi.write(RED_LED, 1)
            camera.save_frame(filename=f"red_{now}.png", folder=f"captures_{now}")
            pi.write(RED_LED, 0)
            
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