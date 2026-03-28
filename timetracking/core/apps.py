import os

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Avoid double-run from Django autoreloader.
        if os.environ.get("RUN_MAIN") != "true":
            return

        from django.conf import settings

        if not settings.DEBUG:
            return

        try:
            from django.contrib.auth import get_user_model

            email = getattr(settings, "AUTO_SUPERUSER_EMAIL", "")
            password = getattr(settings, "AUTO_SUPERUSER_PASSWORD", "")
            if not email or not password:
                return

            User = get_user_model()
            user = User.objects.filter(email=email).first()
            if not user:
                User.objects.create_superuser(email=email, password=password)
            else:
                user.is_staff = True
                user.is_superuser = True
                if getattr(user, "status", None) != "active":
                    user.status = "active"
                user.save()
        except Exception:
            return
