from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('art.urls')),
]

# In development, let the dev server serve uploaded media at MEDIA_URL so the
# local-filesystem storage backend "just works" when you try the app locally.
# In production media is served by the reverse proxy (local backend) or fetched
# from GCS directly; static() returns nothing when DEBUG is False.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'django.views.defaults.page_not_found'
handler500 = 'django.views.defaults.server_error'
