from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.cache import add_never_cache_headers
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


def is_curator(user):
    """The curate gate: an active staff account."""
    return user.is_active and user.is_staff


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = False

    def test_func(self):
        return is_curator(self.request.user)


class PieceQuerysetMixin:
    """Joined piece queryset for the public pages, with drafts excluded.

    Quick-add drafts carry placeholder artist/location and no title, so they must
    never appear on a public page. Lists exclude them for everyone, staff
    included — the gallery is the public artifact, and /curate/ is where a
    curator reviews unfinished work. Only the detail page opts into showing them
    (to staff alone), so a curator can preview a piece before publishing it.
    Guarded by DraftVisibilityTests in test_public_views.py.
    """

    drafts_visible_to_staff = False

    def get_queryset(self):
        qs = Piece.objects.select_related('artist', 'medium', 'location')
        if self.drafts_visible_to_staff and is_curator(self.request.user):
            return qs
        return qs.filter(draft=False)


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
        return super().get_queryset().order_by('artist__name', 'title', 'id')


class LocationView(PieceQuerysetMixin, generic.ListView):
    template_name = 'art/location.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        return super().get_queryset().order_by('location__description', 'title', 'id')


class UntaggedView(StaffRequiredMixin, PieceQuerysetMixin, generic.ListView):
    template_name = 'art/untagged.html'
    context_object_name = 'piece_list'

    def get_queryset(self):
        return super().get_queryset().filter(tagged=False).order_by('location__description', 'title', 'id')


class DetailView(PieceQuerysetMixin, generic.DetailView):
    model = Piece
    template_name = 'art/detail.html'
    # A draft's page 404s for the public but renders for a signed-in curator, so
    # they can check a piece (and reach its Edit link) before publishing it.
    drafts_visible_to_staff = True

    def get(self, request, *args, **kwargs):
        if request.GET.get('from') == 'tag':
            if is_curator(request.user):
                piece = self.get_object()
                if piece.slug_is_provisional():
                    # The template hides the tag-write link for these, but the
                    # handler is reachable directly (a typed or bookmarked URL,
                    # or a tag written before this guard existed). Refuse rather
                    # than freeze an unfinished capture at a throwaway URL.
                    messages.info(
                        request,
                        'Add this piece’s details first — its web address isn’t final yet, '
                        'so a tag written now would stop working.',
                    )
                elif piece.tagged:
                    messages.info(request, f'“{piece.display_title}” was already marked as tagged.')
                else:
                    piece.tagged = True
                    piece.save(update_fields=['tagged'])
                    messages.success(request, f'“{piece.display_title}” is now marked as tagged.')
                return redirect(request.path)
            if not request.user.is_authenticated:
                # An expired session is the likely cause of a tap that does
                # nothing — say so instead of silently rendering the page.
                messages.info(request, 'Sign in to the collection to mark this piece as tagged.')

        response = super().get(request, *args, **kwargs)
        # This URL answers differently per session — 200 for a curator, 404 for
        # everyone else — so it must never land in a shared cache. Django only
        # adds `Vary: Cookie` incidentally (via SessionMiddleware), and a proxy
        # that normalises Vary would otherwise serve a curator's rendered draft
        # page, banner and unpublished image and all, to the public.
        if self.object.draft or request.user.is_authenticated:
            add_never_cache_headers(response)
        return response

    def get_context_data(self, **kwargs):
        # Backfill the image's size (for pieces predating dimension capture) so
        # the template can reserve its box; the model owns the logic.
        self.object.ensure_image_dimensions()
        return super().get_context_data(**kwargs)
