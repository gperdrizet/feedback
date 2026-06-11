from django.urls import path

from . import views

app_name = "grading"

urlpatterns = [
    path("", views.gradebook, name="gradebook"),
    path("about/", views.about, name="about"),
    path("sync/", views.sync_assignment, name="sync_assignment"),
    path("assignments/<int:assignment_pk>/delete/", views.delete_assignment, name="delete_assignment"),
    path("assignments/<int:assignment_pk>/", views.assignment_detail, name="assignment_detail"),
    path("assignments/<int:assignment_pk>/batch-status/", views.assignment_batch_status, name="assignment_batch_status"),
    path("submissions/<int:submission_pk>/", views.submission_detail, name="submission_detail"),
]
