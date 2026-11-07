from django.apps import AppConfig


class FloorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.floor"
    verbose_name = "Factory floor screens"

    def ready(self):
        from . import admin_text
        admin_text.apply()
