"""
URL configuration for feedback_app project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path


def healthz(_request):
    return JsonResponse({"status": "ok"})


def favicon(_request):
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
<rect width='64' height='64' rx='12' fill='#8a3200'/>
<path d='M16 18h32v8H26v10h18v8H26v14h-10V18z' fill='#fff'/>
</svg>"""
    return HttpResponse(svg, content_type="image/svg+xml")

urlpatterns = [
    path('healthz/', healthz),
    path('favicon.ico', favicon),
    path('admin/', admin.site.urls),
    path('', include('grading.urls')),
]
