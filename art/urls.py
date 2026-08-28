from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic.base import RedirectView, TemplateView

from . import views, views_curate

curate_patterns = (
    [
        path(
            'login/',
            auth_views.LoginView.as_view(
                template_name='curate/login.html',
                redirect_authenticated_user=True,
            ),
            name='login',
        ),
        path('logout/', auth_views.LogoutView.as_view(next_page='art:curate:login'), name='logout'),
        path('', RedirectView.as_view(pattern_name='art:curate:piece-list', permanent=False), name='home'),
        path('settings/', views_curate.SettingsView.as_view(), name='settings'),
        path('pieces/', views_curate.PieceListView.as_view(), name='piece-list'),
        path('pieces/add/', views_curate.PieceCreateView.as_view(), name='piece-add'),
        path('pieces/<slug:slug>/edit/', views_curate.PieceUpdateView.as_view(), name='piece-edit'),
        path('pieces/<slug:slug>/delete/', views_curate.PieceDeleteView.as_view(), name='piece-delete'),
        path(
            'pieces/<slug:slug>/documents/add/',
            views_curate.PieceDocumentAddView.as_view(),
            name='document-add',
        ),
        path(
            'pieces/<slug:slug>/documents/<uuid:pk>/',
            views_curate.PieceDocumentDownloadView.as_view(),
            name='document-download',
        ),
        path(
            'pieces/<slug:slug>/documents/<uuid:pk>/thumb/',
            views_curate.PieceDocumentThumbnailView.as_view(),
            name='document-thumb',
        ),
        path(
            'pieces/<slug:slug>/documents/<uuid:pk>/delete/',
            views_curate.PieceDocumentDeleteView.as_view(),
            name='document-delete',
        ),
        path('artists/new/', views_curate.ArtistInlineCreateView.as_view(), name='artist-inline-add'),
        path('mediums/new/', views_curate.MediumInlineCreateView.as_view(), name='medium-inline-add'),
        path('locations/new/', views_curate.LocationInlineCreateView.as_view(), name='location-inline-add'),
        path('artists/', views_curate.ArtistListView.as_view(), name='artist-list'),
        path('artists/add/', views_curate.ArtistCreateView.as_view(), name='artist-add'),
        path('artists/<uuid:pk>/edit/', views_curate.ArtistUpdateView.as_view(), name='artist-edit'),
        path('artists/<uuid:pk>/delete/', views_curate.ArtistDeleteView.as_view(), name='artist-delete'),
        path('mediums/', views_curate.MediumListView.as_view(), name='medium-list'),
        path('mediums/add/', views_curate.MediumCreateView.as_view(), name='medium-add'),
        path('mediums/<uuid:pk>/edit/', views_curate.MediumUpdateView.as_view(), name='medium-edit'),
        path('mediums/<uuid:pk>/delete/', views_curate.MediumDeleteView.as_view(), name='medium-delete'),
        path('locations/', views_curate.LocationListView.as_view(), name='location-list'),
        path('locations/add/', views_curate.LocationCreateView.as_view(), name='location-add'),
        path('locations/<uuid:pk>/edit/', views_curate.LocationUpdateView.as_view(), name='location-edit'),
        path('locations/<uuid:pk>/delete/', views_curate.LocationDeleteView.as_view(), name='location-delete'),
    ],
    'curate',
)


app_name = 'art'
urlpatterns = [
    path('curate/', include(curate_patterns)),
    path('', views.IndexView.as_view(), name='index'),
    path('artists/', views.ArtistsView.as_view(), name='artists'),
    path('location/', views.LocationView.as_view(), name='location'),
    path('untagged/', views.UntaggedView.as_view(), name='untagged'),
    path(
        'robots.txt',
        TemplateView.as_view(template_name='robots.txt', content_type='text/plain'),
        name='robots.txt',
    ),
    path(
        'site.webmanifest',
        # Templated (not a static file) so the PWA name follows the curator's
        # SiteSettings site name instead of a hardcoded one.
        TemplateView.as_view(
            template_name='art/site.webmanifest',
            content_type='application/manifest+json',
        ),
        name='manifest',
    ),
    path('healthz/', views.healthz, name='healthz'),
    path('<slug:slug>/', views.DetailView.as_view(), name='piece'),
]
