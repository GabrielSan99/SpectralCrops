from django.contrib.auth.forms import AuthenticationForm
from .models import *
from django import forms


class CustomAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "As credenciais fornecidas são inválidas!",
        "inactive": "Sua conta está inativa!",
    }

