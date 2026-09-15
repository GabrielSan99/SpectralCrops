from django.contrib import admin

from .models import (
    FilterPosition, GeometricCalibration, ReflectanceCalibration,
    ReflectanceZeroCalibration, Project, DataAcquisition, ROIMeasurement,
    Annotation, CameraSettings, BandParameter,
)

admin.site.register(FilterPosition)
admin.site.register(GeometricCalibration)
admin.site.register(ReflectanceCalibration)
admin.site.register(ReflectanceZeroCalibration)
admin.site.register(Project)
admin.site.register(DataAcquisition)
admin.site.register(ROIMeasurement)
admin.site.register(Annotation)
admin.site.register(CameraSettings)
admin.site.register(BandParameter)
