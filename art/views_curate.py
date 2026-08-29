"""Views for the /curate/ admin interface.

Kept separate from the public views so public and admin concerns don't get
tangled. Every view here is staff-only and uncached.
"""

import json
import logging
import os
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View, generic
from django.views.decorators.cache import never_cache

from .forms import (
    ArtistForm,
    LocationForm,
    MediumForm,
    PieceForm,
    SiteSettingsForm,
    validate_document_upload,
    validate_image_upload,
)
from .models import (
    PLACEHOLDER_LABEL,
    Artist,
    Location,
    Medium,
    Piece,
    PieceDocument,
    SiteSettings,
    placeholder_artist,
    placeholder_location,
)
from .views import StaffRequiredMixin

logger = logging.getLogger(__name__)


def _to_uuid(value):
    """Parse a GET param into a UUID, or None if it isn't a valid one."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _clean_querystring(get_params):
    """Querystring for pagination links: drop 'page' and any empty params so
    the links stay short and shareable (the GET filter forms submit every
    control, including the unset ones)."""
    return urlencode({key: value for key, value in get_params.items() if key != 'page' and value})


def _search_q(get_params):
    """The 'q' search term, NUL-scrubbed (Postgres rejects NUL bytes in string
    literals) and trimmed. '' when absent."""
    return get_params.get('q', '').replace('\x00', '').strip()


@method_decorator(never_cache, name='dispatch')
class CurateBaseView(StaffRequiredMixin):
    """Common base — staff-only, never_cache."""


class SettingsView(CurateBaseView, generic.UpdateView):
    form_class = SiteSettingsForm
    template_name = 'curate/settings_form.html'

    def get_object(self, queryset=None):
        return SiteSettings.load()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = 'settings'
        ctx['page_title'] = 'Settings'
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Settings saved.')
        return response

    def get_success_url(self):
        return reverse('art:curate:settings')


class PieceListView(CurateBaseView, generic.ListView):
    template_name = 'curate/piece_list.html'
    context_object_name = 'pieces'
    paginate_by = 48

    # sort value -> (dropdown label, order_by args). Insertion order is the
    # order the options appear in; the first key is the default.
    # Every ordering ends in 'id'. Quick add creates rows that tie COMPLETELY on
    # the other keys (one shared placeholder artist, no title), and a paginated
    # query whose sort has ties is free to order the pages inconsistently — so a
    # curator working the queue would see captures twice, or never. Django's own
    # admin appends '-pk' for exactly this reason.
    SORTS = {
        'artist': ('Artist A–Z', ('artist__name', 'title', 'id')),
        'title': ('Title A–Z', ('title', 'id')),
        'recent': ('Recently acquired', (F('date_acquired').desc(nulls_last=True), 'title', 'id')),
        'oldest': ('Oldest acquired', (F('date_acquired').asc(nulls_last=True), 'title', 'id')),
        'added': ('Recently added', (F('created').desc(nulls_last=True), 'title', 'id')),
    }
    DEFAULT_SORT = 'artist'

    # Presence filters for the "More" panel: find pieces that have — or are
    # missing — each optional bit of data. The value is the Q that matches when
    # the field is MISSING; "has" is simply its negation (~q).
    #
    # MAINTENANCE: this is meant to cover EVERY optional field a curator can set
    # on a Piece. Whenever you add such a field to the model + form, add a
    # presence filter here too. test_presence_filters_cover_every_optional_field
    # fails until you do (or explicitly exclude the field there).
    PRESENCE_FILTERS = {
        # Title is required on the piece form, but Quick add captures photos
        # without one — so "Title: absent" is the queue of captures still
        # needing their details, in either Quick add mode.
        'title': ('Title', Q(title='')),
        'image': ('Image', Q(image='') | Q(image__isnull=True)),
        'medium': ('Medium', Q(medium__isnull=True)),
        'medium_details': ('Medium details', Q(medium_details='')),
        'dimensions': ('Dimensions', Q(width__isnull=True) & Q(height__isnull=True)),
        'date_acquired': ('Acquired date', Q(date_acquired__isnull=True)),
        'acquired': ('Acquired from', Q(acquired='')),
        'purchase_price': ('Purchase price', Q(purchase_price__isnull=True)),
        'website': ('Website', Q(website='')),
        'notes': ('Public notes', Q(notes='')),
        'notes_private': ('Private notes', Q(notes_private='')),
        # Documents are a reverse relation, so use an Exists subquery — a join
        # (Q(documents__isnull=...)) would multiply a piece's row by its document
        # count for the "has" case. missing_q here = "has no documents".
        'documents': ('Documents', ~Exists(PieceDocument.objects.filter(piece=OuterRef('pk')))),
    }

    def get_queryset(self):
        qs = Piece.objects.select_related('artist', 'medium', 'location')

        q = _search_q(self.request.GET)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(artist__name__icontains=q) | Q(notes__icontains=q))

        for field, param in (('artist_id', 'artist'), ('location_id', 'location'), ('medium_id', 'medium')):
            value = _to_uuid(self.request.GET.get(param))
            if value is not None:
                qs = qs.filter(**{field: value})

        tagged = self.request.GET.get('tagged')
        if tagged == 'yes':
            qs = qs.filter(tagged=True)
        elif tagged == 'no':
            qs = qs.filter(tagged=False)

        # Drafts (quick-add captures awaiting their metadata) are the working
        # queue: "Drafts" is the list a curator sits with after a photo walk.
        draft = self.request.GET.get('draft')
        if draft == 'yes':
            qs = qs.filter(draft=True)
        elif draft == 'no':
            qs = qs.filter(draft=False)

        for key, (_label, missing_q) in self.PRESENCE_FILTERS.items():
            value = self.request.GET.get(f'has_{key}')
            if value == 'yes':
                qs = qs.filter(~missing_q)
            elif value == 'no':
                qs = qs.filter(missing_q)

        sort = self.request.GET.get('sort') or self.DEFAULT_SORT
        order = self.SORTS.get(sort, self.SORTS[self.DEFAULT_SORT])[1]
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = 'pieces'
        ctx['q'] = self.request.GET.get('q', '')
        ctx['selected_artist'] = self.request.GET.get('artist', '')
        ctx['selected_location'] = self.request.GET.get('location', '')
        ctx['selected_medium'] = self.request.GET.get('medium', '')
        ctx['selected_tagged'] = self.request.GET.get('tagged', '')
        ctx['selected_draft'] = self.request.GET.get('draft', '')
        ctx['sort_options'] = [(value, label) for value, (label, _order) in self.SORTS.items()]
        ctx['selected_sort'] = self.request.GET.get('sort') or self.DEFAULT_SORT

        # "More" panel: one tri-state (Any / Present / Absent) control per filter.
        presence = []
        for key, (label, _missing_q) in self.PRESENCE_FILTERS.items():
            value = self.request.GET.get(f'has_{key}', '')
            if value not in ('yes', 'no'):
                value = ''  # ignore anything we don't recognise
            presence.append({'key': key, 'label': label, 'value': value})
        advanced_count = sum(1 for f in presence if f['value'])
        advanced_active = bool(advanced_count)
        ctx['presence_filters'] = presence
        ctx['advanced_count'] = advanced_count
        ctx['advanced_active'] = advanced_active

        ctx['is_filtered'] = bool(
            ctx['q']
            or ctx['selected_artist']
            or ctx['selected_location']
            or ctx['selected_medium']
            or ctx['selected_tagged']
            or ctx['selected_draft']
            or advanced_active
        )

        # Dropdowns show every FK that has pieces, plus the one currently in the
        # URL even if it has none — otherwise the <select> falls back to "All"
        # while the URL still filters, leaving the user no way to see why
        # results are empty.
        ctx['artists'] = self._dropdown_options(Artist, 'name', ctx['selected_artist'])
        ctx['locations'] = self._dropdown_options(Location, 'description', ctx['selected_location'])
        ctx['mediums'] = self._dropdown_options(Medium, 'description', ctx['selected_medium'])

        ctx['querystring'] = _clean_querystring(self.request.GET)
        return ctx

    @staticmethod
    def _dropdown_options(model, order_field, selected_str):
        condition = Q(piece__isnull=False)
        selected_uuid = _to_uuid(selected_str)
        if selected_uuid is not None:
            condition |= Q(id=selected_uuid)
        return model.objects.filter(condition).order_by(order_field).distinct()


class PieceCreateView(CurateBaseView, generic.CreateView):
    model = Piece
    form_class = PieceForm
    template_name = 'curate/piece_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = 'pieces'
        ctx['mode'] = 'add'
        ctx['page_title'] = 'Add piece'
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Added “{self.object.display_title}”.')
        return response

    def get_success_url(self):
        # "Save & add another" returns to a blank form for batch entry;
        # otherwise stay in the admin on the new piece's edit page (where
        # "View on site →" leads to the public page and its NFC-write link).
        if '_addanother' in self.request.POST:
            return reverse('art:curate:piece-add')
        return reverse('art:curate:piece-edit', kwargs={'slug': self.object.slug})


class PieceUpdateView(CurateBaseView, generic.UpdateView):
    model = Piece
    form_class = PieceForm
    template_name = 'curate/piece_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = 'pieces'
        ctx['mode'] = 'edit'
        # A quick-add draft has no title yet, so fall back to a placeholder
        # heading rather than rendering an empty <h1>.
        ctx['page_title'] = self.object.title or 'Untitled draft'
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Saved changes to “{self.object.display_title}”.')
        return response

    def get_success_url(self):
        return reverse('art:curate:piece-edit', kwargs={'slug': self.object.slug})


class PieceDeleteView(CurateBaseView, generic.DeleteView):
    model = Piece
    template_name = 'curate/piece_confirm_delete.html'
    success_url = reverse_lazy('art:curate:piece-list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = 'pieces'
        return ctx

    def form_valid(self, form):
        # Capture the title before the row is gone, then confirm the delete.
        title = self.object.title
        response = super().form_valid(form)
        messages.success(self.request, f'Deleted “{title}”.')
        return response


# Extensions we serve inline (viewable in a browser tab). Anything else is
# forced to download. The Content-Type is derived from the extension — NOT the
# upload-supplied content_type — so a mislabeled file can't be rendered as
# something dangerous inline (and SECURE_CONTENT_TYPE_NOSNIFF stops the browser
# sniffing past it).
_INLINE_CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.heic': 'image/heic',
    '.heif': 'image/heif',
}


class QuickAddView(CurateBaseView, View):
    """Capture pieces as bare photos — for cataloguing a room with a phone.

    Each photo becomes a DRAFT piece: placeholder artist/location (a Piece can't
    have neither — see PLACEHOLDER_LABEL in models.py), no title, hidden from the
    public site until the curator completes it. The point is to get the artwork
    *recorded* in one pass and do the metadata later, at a desk.

    Photos go through the same `validate_image_upload` as the regular form, so
    the format allowlist, size cap, and fail-closed EXIF strip all still apply —
    nothing here is a looser way into storage. A rejected photo is reported and
    skipped rather than failing the whole batch, so one odd file in twenty
    doesn't cost the walk.
    """

    template_name = 'curate/quick_add.html'
    # How many just-captured drafts to show back as reassurance ("it worked").
    RECENT_LIMIT = 12

    def get(self, request):
        return render(request, self.template_name, self._context())

    def post(self, request):
        photos = request.FILES.getlist('photos')
        if not photos:
            messages.info(request, 'No photos were selected.')
            return redirect('art:curate:quick-add')

        as_draft = SiteSettings.load().quick_add_drafts
        artist = location = None
        added = failed = 0
        for upload in photos:
            try:
                validate_image_upload(upload)
            except ValidationError as exc:
                messages.error(request, f'{upload.name}: {" ".join(exc.messages)}')
                continue
            # Resolved on first use, not up front: a batch where every photo is
            # rejected should not leave two permanent "Not set" rows behind in
            # the artist and location lists. Shared by the whole walk thereafter,
            # so the curator can reassign it in one filtered pass.
            if artist is None:
                artist, location = placeholder_artist(), placeholder_location()
            try:
                # Each photo is its own savepoint. ATOMIC_REQUESTS wraps the whole
                # POST, so without this a storage hiccup on photo 40 of 60 would
                # roll back all 39 that had already succeeded — losing the walk
                # and orphaning their files. Only the failing photo is lost.
                with transaction.atomic():
                    Piece.objects.create(image=upload, title='', artist=artist, location=location, draft=as_draft)
            except Exception:
                # Deliberately broad: the point is that ONE bad photo must not
                # cost the batch. Logged (Sentry, if configured) so a systematic
                # failure is still visible rather than silently swallowed.
                logger.exception('Quick add failed to store %s', upload.name)
                failed += 1
                messages.error(request, f'{upload.name}: could not be saved — try it again on its own.')
                continue
            added += 1

        if added:
            noun = 'draft' if as_draft else 'piece'
            tail = (
                'They stay off the public site until you fill in their details.'
                if as_draft
                else 'They are live on the public site now — add their details when you can.'
            )
            messages.success(request, f'Added {added} {noun}{"" if added == 1 else "s"}. {tail}')
        return redirect('art:curate:quick-add')

    def _context(self):
        # Two overlapping backlogs, counted separately because they answer
        # different questions and have different filtered views: untitled
        # captures still need their details, drafts are still hidden from the
        # public site. A batch can be one without the other — titled but not yet
        # published, or (in publish-immediately mode) live but nameless.
        untitled = Piece.objects.filter(title='')
        drafts = Piece.objects.filter(draft=True)
        pending = Piece.objects.filter(Q(title='') | Q(draft=True))
        return {
            'active': 'pieces',
            'page_title': 'Quick add',
            'as_draft': SiteSettings.load().quick_add_drafts,
            # Nulls last: `created` is null only for pieces predating the column,
            # which by definition aren't fresh captures.
            'recent_captures': pending.order_by(F('created').desc(nulls_last=True), 'id')[: self.RECENT_LIMIT],
            'unfinished_count': untitled.count(),
            'draft_count': drafts.count(),
            'placeholder_label': PLACEHOLDER_LABEL,
            # The multipart parser rejects a request with more files than this
            # before the view runs, so the page enforces it client-side with a
            # real message instead of letting the batch die as a bare 400.
            'max_files': getattr(settings, 'DATA_UPLOAD_MAX_NUMBER_FILES', 100),
        }


class PieceDocumentDownloadView(CurateBaseView, View):
    """Serve a piece's private document to staff. Opens inline (so the curate
    list can view it in a new tab); ?download=1 forces a download. The file has
    no public URL, so this staff-gated view (CurateBaseView → is_staff +
    is_active) is the ONLY way to reach it — anonymous/non-staff are redirected
    or 403'd, never given the bytes. Unknown extensions are forced to download
    so nothing unexpected is rendered inline."""

    def get(self, request, slug, pk):
        doc = get_object_or_404(PieceDocument, pk=pk, piece__slug=slug)
        filename = doc.original_name or doc.file.name.rsplit('/', 1)[-1]
        inline_type = _INLINE_CONTENT_TYPES.get(os.path.splitext(filename)[1].lower())
        as_attachment = bool(request.GET.get('download')) or inline_type is None
        response = FileResponse(doc.file.open('rb'), as_attachment=as_attachment, filename=filename)
        if not as_attachment:
            response['Content-Type'] = inline_type
        return response


class PieceDocumentThumbnailView(CurateBaseView, View):
    """Stream a document's private thumbnail to staff for the curate list's
    <img>. Like the download view it's staff-gated and the file has no public
    URL — the staff session cookie on the <img> request authorizes it. 404 when
    the document has no thumbnail (the template only renders the <img> when it
    does, so this is just defensive)."""

    def get(self, request, slug, pk):
        doc = get_object_or_404(PieceDocument, pk=pk, piece__slug=slug)
        if not doc.thumbnail:
            raise Http404('No thumbnail for this document.')
        return FileResponse(doc.thumbnail.open('rb'), content_type='image/jpeg')


class PieceDocumentAddView(CurateBaseView, View):
    """Attach one or more uploaded documents to a piece, then return to its edit
    page. Each file is size/type validated; a rejected file is reported and
    skipped rather than failing the whole upload."""

    def post(self, request, slug):
        piece = get_object_or_404(Piece, slug=slug)
        files = request.FILES.getlist('documents')
        if not files:
            messages.info(request, 'No files were selected.')
            return redirect('art:curate:piece-edit', slug=slug)
        added = 0
        for upload in files:
            try:
                validate_document_upload(upload)
            except ValidationError as exc:
                messages.error(request, f'{upload.name}: {"; ".join(exc.messages)}')
                continue
            doc = PieceDocument.objects.create(
                piece=piece,
                file=upload,
                original_name=upload.name[:255],
                content_type=getattr(upload, 'content_type', '') or '',
                size=upload.size,
                uploaded_by=request.user,
            )
            doc.generate_thumbnail()  # best-effort: returns False (no thumbnail) without raising
            added += 1
        if added:
            messages.success(request, f'Added {added} document{"" if added == 1 else "s"}.')
        return redirect('art:curate:piece-edit', slug=slug)


class PieceDocumentDeleteView(CurateBaseView, View):
    """Delete one document (its file is removed by the post_delete signal)."""

    def post(self, request, slug, pk):
        doc = get_object_or_404(PieceDocument, pk=pk, piece__slug=slug)
        doc.delete()
        messages.success(request, 'Document removed.')
        return redirect('art:curate:piece-edit', slug=slug)


class InlineCreateView(CurateBaseView, View):
    """HTMX endpoint that renders a modal form for one FK type and, on save,
    fires a 'modelCreated' browser event so the parent piece form can splice
    the new option into the matching <select>.

    Subclasses define `form_class`, `model_label`, and `display_attr`.
    """

    template_name = 'curate/_inline_create.html'
    form_class = None
    model_label = ''  # "artist" / "medium" / "location"
    display_attr = 'name'  # attribute used as the option label
    # Namespace the modal field ids so the full artist form's website/notes don't
    # collide (duplicate DOM ids) with the same-named fields on the piece form
    # underneath. Field NAMES stay unprefixed, so POST handling is unchanged.
    form_auto_id = 'id_inline_%s'

    def _ctx(self, request, form):
        target = request.GET.get('target', '')
        submit_url = request.path
        if target:
            submit_url = f'{submit_url}?{urlencode({"target": target})}'
        return {
            'form': form,
            'title': f'New {self.model_label}',
            'target': target,
            'submit_url': submit_url,
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._ctx(request, self.form_class(auto_id=self.form_auto_id)))

    def post(self, request, *args, **kwargs):
        target = request.GET.get('target', '')
        # request.FILES so a file-bearing inline form (the artist portrait) saves
        # its upload; harmless for the file-less medium/location forms.
        form = self.form_class(request.POST, request.FILES, auto_id=self.form_auto_id)
        if form.is_valid():
            obj = form.save()
            response = HttpResponse('')
            # NOTE: htmx 2.x intercepts a top-level 'target' key inside the
            # HX-Trigger event payload as a retargeting selector. Use a
            # different key (select_id) and let curate.js read it.
            response['HX-Trigger'] = json.dumps(
                {
                    'modelCreated': {
                        'select_id': target,
                        'id': str(obj.id),
                        'name': getattr(obj, self.display_attr) or str(obj),
                    }
                }
            )
            return response
        # htmx 2.x's default responseHandling does NOT swap 4xx responses, so
        # validation re-renders go back as 200 to ensure the bound form (with
        # errors) replaces the modal contents.
        return render(request, self.template_name, self._ctx(request, form))


class ArtistInlineCreateView(InlineCreateView):
    form_class = ArtistForm  # full artist form (name, portrait, links, notes)
    model_label = 'artist'
    display_attr = 'name'


class MediumInlineCreateView(InlineCreateView):
    form_class = MediumForm
    model_label = 'medium'
    display_attr = 'description'


class LocationInlineCreateView(InlineCreateView):
    form_class = LocationForm
    model_label = 'location'
    display_attr = 'description'


# ---------- Artist / Medium / Location list + CRUD ----------
#
# The three lookup models share one CRUD shape, so the behaviour lives in the
# _Lookup* base views and each concrete view only declares config. A per-model
# mixin (_ArtistCrud etc.) supplies:
#   model         the model class
#   active        nav key + plural context_object_name (e.g. 'artists')
#   label         singular; drives the art:curate:<label>-{list,add,edit,delete} URLs
#   display_field model attribute used as the object's human name


class _LookupCrudMixin:
    active = ''
    label = ''
    display_field = 'description'

    def get_success_url(self):
        return reverse(f'art:curate:{self.label}-list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active'] = self.active
        return ctx


class _LookupListView(_LookupCrudMixin, CurateBaseView, generic.ListView):
    order_field = 'description'
    paginate_by = 50

    SORTS = {
        'name': 'Name A–Z',
        'most': 'Most pieces',
        'fewest': 'Fewest pieces',
    }
    DEFAULT_SORT = 'name'

    def get_queryset(self):
        qs = self.model.objects.annotate(piece_count=Count('piece'))

        q = _search_q(self.request.GET)
        if q:
            qs = qs.filter(**{f'{self.order_field}__icontains': q})

        sort = self.request.GET.get('sort') or self.DEFAULT_SORT
        if sort == 'most':
            return qs.order_by('-piece_count', self.order_field)
        if sort == 'fewest':
            return qs.order_by('piece_count', self.order_field)
        return qs.order_by(self.order_field)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['sort_options'] = list(self.SORTS.items())
        ctx['selected_sort'] = self.request.GET.get('sort') or self.DEFAULT_SORT
        ctx['is_filtered'] = bool(ctx['q'])
        ctx['querystring'] = _clean_querystring(self.request.GET)
        return ctx


class _LookupCreateView(_LookupCrudMixin, CurateBaseView, generic.CreateView):
    template_name = 'curate/_crud_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Added {self.label} “{getattr(self.object, self.display_field)}”.')
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['mode'] = 'add'
        ctx['page_title'] = f'New {self.label}'
        ctx['back_url'] = self.get_success_url()
        return ctx


class _LookupUpdateView(_LookupCrudMixin, CurateBaseView, generic.UpdateView):
    template_name = 'curate/_crud_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Saved changes to {self.label} “{getattr(self.object, self.display_field)}”.')
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['mode'] = 'edit'
        ctx['page_title'] = getattr(self.object, self.display_field)
        ctx['back_url'] = self.get_success_url()
        ctx['delete_url'] = reverse(f'art:curate:{self.label}-delete', kwargs={'pk': self.object.pk})
        return ctx


class _LookupDeleteView(_LookupCrudMixin, CurateBaseView, generic.DeleteView):
    template_name = 'curate/_crud_confirm_delete.html'

    def form_valid(self, form):
        label_text = getattr(self.object, self.display_field)
        try:
            response = super().form_valid(form)
        except ProtectedError:
            # The model FKs are PROTECT, so a referenced lookup can't be
            # deleted. The confirm page hides the button in this case, but a
            # crafted/raced POST still lands here — answer it gracefully.
            count = self.object.piece_set.count()
            pieces = f'{count} piece' if count == 1 else f'{count} pieces'
            reassign = 'it' if count == 1 else 'them'
            messages.error(
                self.request,
                f'Can’t delete {self.label} “{label_text}” — still referenced by {pieces}. Reassign {reassign} first.',
            )
            return redirect(self.get_success_url())
        messages.success(self.request, f'Deleted {self.label} “{label_text}”.')
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object_label'] = getattr(self.object, self.display_field)
        ctx['label'] = self.label
        ctx['piece_count'] = self.object.piece_set.count()
        ctx['back_url'] = reverse(f'art:curate:{self.label}-edit', kwargs={'pk': self.object.pk})
        return ctx


class _ArtistCrud:
    model = Artist
    active = 'artists'
    label = 'artist'
    display_field = 'name'


class ArtistListView(_ArtistCrud, _LookupListView):
    template_name = 'curate/artist_list.html'
    context_object_name = 'artists'
    order_field = 'name'


class ArtistCreateView(_ArtistCrud, _LookupCreateView):
    form_class = ArtistForm
    template_name = 'curate/artist_form.html'  # adds a portrait dropzone + preview


class ArtistUpdateView(_ArtistCrud, _LookupUpdateView):
    form_class = ArtistForm
    template_name = 'curate/artist_form.html'


class ArtistDeleteView(_ArtistCrud, _LookupDeleteView):
    pass


class _MediumCrud:
    model = Medium
    active = 'mediums'
    label = 'medium'
    display_field = 'description'


class MediumListView(_MediumCrud, _LookupListView):
    template_name = 'curate/medium_list.html'
    context_object_name = 'mediums'


class MediumCreateView(_MediumCrud, _LookupCreateView):
    form_class = MediumForm


class MediumUpdateView(_MediumCrud, _LookupUpdateView):
    form_class = MediumForm


class MediumDeleteView(_MediumCrud, _LookupDeleteView):
    pass


class _LocationCrud:
    model = Location
    active = 'locations'
    label = 'location'
    display_field = 'description'


class LocationListView(_LocationCrud, _LookupListView):
    template_name = 'curate/location_list.html'
    context_object_name = 'locations'


class LocationCreateView(_LocationCrud, _LookupCreateView):
    form_class = LocationForm


class LocationUpdateView(_LocationCrud, _LookupUpdateView):
    form_class = LocationForm


class LocationDeleteView(_LocationCrud, _LookupDeleteView):
    pass
