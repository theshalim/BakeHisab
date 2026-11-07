from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from apps.floor.views import LoginView

admin.site.site_header = "বেকহিসাব — সেটআপ (BakeHisab Setup)"
admin.site.site_title = "বেকহিসাব (BakeHisab) সেটআপ"
admin.site.index_title = "সেটআপ: কারখানা, পণ্য, রেসিপি, দাম (Setup: Factory, Product, Recipe, Price)"

urlpatterns = [
    # one login page for everyone (the navy design); admin's own login/logout send people there
    path("admin/login/", RedirectView.as_view(url="/login/", query_string=True)),
    path("admin/logout/", auth_views.LogoutView.as_view()),
    path("admin/", admin.site.urls),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.floor.urls")),
]
