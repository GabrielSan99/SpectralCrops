from django.shortcuts import render
from django.contrib.auth.decorators import login_required

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