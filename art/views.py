from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views import generic
from django.views.decorators.cache import never_cache

from .models import Piece, SiteSettings


@transaction.non_atomic_requests  # don't open a DB transaction (ATOMIC_REQUESTS)
@never_cache
def healthz(request):
    """Liveness probe: a cheap 200 that touches no DB or storage, so a container
    healthcheck restarts only on a genuinely dead process — not on a slow/down DB
    or storage backend. Exempt from ATOMIC_REQUESTS (else even this would need a
    transaction) and from the SSL redirect (SECURE_REDIRECT_EXEMPT), so the
    in-container HTTP check gets a real 200."""
    return HttpResponse('ok', content_type='text/plain')


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = False

    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


class PieceQuerysetMixin:
    def get_queryset(self):
        return Piece.objects.select_related('artist', 'medium', 'location')


class IndexView(PieceQuerysetMixin, generic.ListView):
    template_name = 'art/index.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        # Order is curator-set on the Settings page (not visitor-selectable).
        return super().get_queryset().order_by(*SiteSettings.load().public_ordering())


class ArtistsView(PieceQuerysetMixin, generic.ListView):
    template_name = 'art/artists.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        return super().get_queryset().order_by('artist__name', 'title')


class LocationView(PieceQuerysetMixin, generic.ListView):
    template_name = 'art/location.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        return super().get_queryset().order_by('location__description', 'title')


class UntaggedView(StaffRequiredMixin, PieceQuerysetMixin, generic.ListView):
    template_name = 'art/untagged.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        return super().get_queryset().filter(tagged=False).order_by('location__description', 'title')


class DetailView(PieceQuerysetMixin, generic.DetailView):
    model = Piece
    template_name = 'art/detail.html'

    def get(self, request, *args, **kwargs):
        if request.GET.get('from') == 'tag':
            if request.user.is_active and request.user.is_staff:
                piece = self.get_object()
                if piece.tagged:
                    messages.info(request, f'“{piece.title}” was already marked as tagged.')
                else:
                    piece.tagged = True
                    piece.save(update_fields=['tagged'])
                    messages.success(request, f'“{piece.title}” is now marked as tagged.')
                return redirect(request.path)
            if not request.user.is_authenticated:
                # An expired session is the likely cause of a tap that does
                # nothing — say so instead of silently rendering the page.
                messages.info(request, 'Sign in to the collection to mark this piece as tagged.')

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        # Backfill the image's size (for pieces predating dimension capture) so
        # the template can reserve its box; the model owns the logic.
        self.object.ensure_image_dimensions()
        return super().get_context_data(**kwargs)
