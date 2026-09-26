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
    path('video_feed/stop/', views.video_feed_stop, name='video_feed_stop'),
    path('img_segmentation/', views.img_segmentation, name='img_segmentation'),

    path('tests/', views.tests, name="tests"),
    # API do painel de tests
    path('tests/status/', views.tests_status, name='tests_status'),
    path('tests/led/', views.tests_led, name='tests_led'),
    path('tests/leds_off/', views.tests_leds_off, name='tests_leds_off'),
    path('tests/motor/', views.tests_motor, name='tests_motor'),
    path('tests/motor_reset/', views.tests_motor_reset, name='tests_motor_reset'),
    path('tests/capture/', views.tests_capture, name='tests_capture'),

    # Data Acquisition
    path('data_acquisition/', views.data_acquisition, name='data_acquisition'),
    path('data_acquisition/capture/', views.data_acquisition_capture, name='data_acquisition_capture'),
    path('data_acquisition/save/', views.data_acquisition_save, name='data_acquisition_save'),
    path('data_acquisition/<int:acq_id>/delete/', views.data_acquisition_delete, name='data_acquisition_delete'),
    path('data_acquisition/download_zip/', views.data_acquisition_download_zip, name='data_acquisition_download_zip'),

    # Projetos (selecao/criacao na Home)
    path('projects/create/', views.project_create, name='project_create'),
    path('projects/select/', views.project_select, name='project_select'),
    path('projects/<int:project_id>/delete/', views.project_delete, name='project_delete'),
    path('projects/<int:project_id>/model/', views.project_model_upload, name='project_model_upload'),

    # Annotations (grade de aquisicoes do projeto ativo + editor de anotacao)
    path('annotations/', views.annotations_list, name='annotations_list'),
    path('annotations/export/yolo/boxes/', views.export_yolo_boxes, name='export_yolo_boxes'),
    path('annotations/export/yolo/seg/', views.export_yolo_seg, name='export_yolo_seg'),
    path('annotations/export/points/', views.export_points, name='export_points'),
    path('annotations/export/classification/', views.export_classification, name='export_classification'),
    path('annotations/<int:acq_id>/', views.annotate_view, name='annotate_view'),
    path('annotations/<int:acq_id>/save/', views.annotation_save, name='annotation_save'),
    path('annotations/<int:acq_id>/rois/compute/', views.roi_measurement_compute, name='roi_measurement_compute'),
    path('annotations/<int:acq_id>/label/', views.annotation_image_label, name='annotation_image_label'),
    path('annotations/<int:acq_id>/auto/otsu/', views.auto_segment_otsu, name='auto_segment_otsu'),
    path('annotations/<int:acq_id>/auto/boxes_to_polygons/', views.auto_polygon_from_boxes, name='auto_polygon_from_boxes'),
    path('annotations/<int:acq_id>/auto/det/', views.auto_segment_det, name='auto_segment_det'),
    path('annotations/<int:acq_id>/auto/yolo/', views.auto_segment_yolo, name='auto_segment_yolo'),
    path('annotations/<int:acq_id>/auto/classify/', views.auto_classify, name='auto_classify'),

    # Analysis (metricas/filtros a partir das medicoes por ROI -- ver Annotations acima)
    path('analysis/', views.roi_measurements_view, name='roi_measurements_view'),
    path('analysis/export/', views.export_roi_measurements, name='export_roi_measurements'),

    # Parametrizacao
    path('parameterization/', views.parameterization, name='parameterization'),
    path('parameterization/motor/', views.param_motor, name='param_motor'),
    path('parameterization/posicionar/', views.param_posicionar, name='param_posicionar'),
    path('parameterization/geo_frame/', views.param_geo_frame, name='param_geo_frame'),
    path('parameterization/reflectance/capture/', views.param_reflectance_capture, name='param_reflectance_capture'),
    path('parameterization/reflectance/compute/', views.param_reflectance_compute, name='param_reflectance_compute'),
    path('parameterization/reflectance/zero/', views.param_reflectance_zero, name='param_reflectance_zero'),
    path('parameterization/bands/auto_expose/', views.param_band_auto_expose, name='param_band_auto_expose'),
    path('parameterization/save/', views.param_save, name='param_save'),
    path('parameterization/camera/save/', views.param_camera_save, name='param_camera_save'),
    path('parameterization/camera/reset/', views.param_camera_reset, name='param_camera_reset'),
]

# serve os arquivos de MEDIA (imagens de calibracao) no modo dev
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
