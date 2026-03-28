from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.admin_urls if hasattr(admin.site, 'admin_urls') else admin.site.urls),
    # Keep admin accessible under /core/ route family too.
    # Redirect instead of double-including to avoid URL namespace conflicts.
    path('core/admin/', RedirectView.as_view(url='/admin/', permanent=False)),
    # Redirect localhost:8000 directly to localhost:8000/core/
    path('', RedirectView.as_view(url='/core/', permanent=False)),
    # Mount all core URLs under /core/
    path('core/', include('core.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

