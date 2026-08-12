from django.contrib import admin
from django.urls import path, include
from spectral_app import views

from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from spectral_app.forms import CustomAuthenticationForm

urlpatterns = [
    path("accounts/", include("django.contrib.auth.urls")),
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=CustomAuthenticationForm),
        name="login",
    ),

    path('admin/', admin.site.urls),
    path('', views.index, name="index"),

    path('video_feed/', views.video_feed, name='video_feed'),
    path('img_segmentation/', views.img_segmentation, name='img_segmentation'),

    path('tests/', views.tests, name="tests"),
    # API do painel de tests
    path('tests/status/', views.tests_status, name='tests_status'),
    path('tests/led/', views.tests_led, name='tests_led'),
    path('tests/leds_off/', views.tests_leds_off, name='tests_leds_off'),
    path('tests/motor/', views.tests_motor, name='tests_motor'),
    path('tests/motor_reset/', views.tests_motor_reset, name='tests_motor_reset'),
    path('tests/capture/', views.tests_capture, name='tests_capture'),

    # Parametrizacao
    path('parameterization/', views.parameterization, name='parameterization'),
    path('parameterization/motor/', views.param_motor, name='param_motor'),
    path('parameterization/posicionar/', views.param_posicionar, name='param_posicionar'),
    path('parameterization/geo_frame/', views.param_geo_frame, name='param_geo_frame'),
    path('parameterization/save/', views.param_save, name='param_save'),
]

# serve os arquivos de MEDIA (imagens de calibracao) no modo dev
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
