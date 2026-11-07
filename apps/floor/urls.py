from django.urls import path

from . import views

app_name = "floor"
urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.plan_form, name="new"),
    path("p/<int:pk>/", views.plan_detail, name="detail"),
    path("p/<int:pk>/edit/", views.plan_form, name="edit"),
    path("p/<int:pk>/finish/", views.finish, name="finish"),
    path("p/<int:pk>/correct/", views.correct, name="correct"),
    path("p/<int:pk>/history/", views.history, name="history"),
    path("p/<int:pk>/print/", views.print_plan, name="print"),
]
