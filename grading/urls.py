from django.urls import path

from . import views

app_name = "grading"

urlpatterns = [
    path("", views.gradebook, name="gradebook"),
    path("sync/", views.sync_assignment, name="sync_assignment"),
    path("assignments/<int:assignment_pk>/", views.assignment_detail, name="assignment_detail"),
    path("submissions/<int:submission_pk>/", views.submission_detail, name="submission_detail"),
]
