from core.models import Notification


def navbar_notifications(request):
    if not request.user.is_authenticated:
        return {
            "navbar_notifications": [],
            "navbar_unread_count": 0,
        }

    notifications = (
        Notification.objects.filter(user=request.user)
        .order_by("-created_at")[:8]
    )
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    return {
        "navbar_notifications": notifications,
        "navbar_unread_count": unread_count,
    }
