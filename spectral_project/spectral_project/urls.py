from django.contrib import admin
from django.urls import path, include
from spectral_app import views

from django.contrib.auth import views as auth_views
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




    path('tests/', views.tests, name="tests"),
]
