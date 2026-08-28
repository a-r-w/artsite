"""Tests for staff-only piece documents (private files served via a gated view).

The access-control gate matrix (anonymous → login, non-staff → 403, staff → 200)
is covered for the download URL in test_security.py; these tests cover the
behaviour: staff get the real bytes, and nothing leaks to the public site.
"""

from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from art.models import PieceDocument

from .factories import make_document, make_piece, make_staff, make_user, tiny_png


class DocumentDownloadTests(TestCase):
    def _url(self, doc):
        return reverse('art:curate:document-download', kwargs={'slug': doc.piece.slug, 'pk': doc.pk})

    def test_staff_can_view_the_file_inline(self):
        doc = make_document(make_piece(), name='receipt.pdf', content=b'%PDF-1.4 secret receipt')
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(doc))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.4 secret receipt')
        self.assertEqual(resp['Content-Type'], 'application/pdf')  # from the extension, not the upload
        self.assertIn('inline', resp['Content-Disposition'])  # opens in the browser tab

    def test_download_param_forces_an_attachment(self):
        doc = make_document(make_piece(), name='receipt.pdf', content=b'%PDF-1.4 x')
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(doc) + '?download=1')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn('receipt.pdf', resp['Content-Disposition'])

    def test_unknown_extension_is_forced_to_download_not_rendered_inline(self):
        # A non-allowlisted extension can't be served inline (so it can't render
        # as HTML/script in the staff session) — it's forced to attachment.
        doc = make_document(make_piece(), name='note.html', content=b'<b>hi</b>')
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(doc))
        self.assertIn('attachment', resp['Content-Disposition'])

    def test_inline_content_type_is_from_the_extension_not_the_stored_upload_type(self):
        # The stored upload content_type is untrusted: a .pdf mislabeled
        # text/html is still served inline as application/pdf, never text/html.
        doc = make_document(make_piece(), name='evil.pdf', content=b'%PDF-1.4 x', content_type='text/html')
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(doc))
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('inline', resp['Content-Disposition'])

    def test_download_response_sets_nosniff(self):
        # The browser-side half of the inline defense: don't MIME-sniff past the
        # declared type.
        doc = make_document(make_piece(), name='receipt.pdf', content=b'%PDF x')
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(doc))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_anonymous_is_redirected_not_served(self):
        doc = make_document(make_piece())
        resp = self.client.get(self._url(doc))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/curate/login/', resp.url)

    def test_non_staff_is_forbidden(self):
        doc = make_document(make_piece())
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self._url(doc)).status_code, 403)

    def test_documents_never_leak_onto_the_public_detail_page(self):
        piece = make_piece(title='Has Docs')
        make_document(piece, name='private-receipt.pdf')
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'private-receipt.pdf')  # filename not exposed
        self.assertNotContains(resp, '/documents/')  # no download link on the public page


class DocumentAddTests(TestCase):
    def setUp(self):
        self.piece = make_piece()
        self.client.force_login(make_staff())
        self.add_url = reverse('art:curate:document-add', kwargs={'slug': self.piece.slug})
        self.edit_url = reverse('art:curate:piece-edit', kwargs={'slug': self.piece.slug})

    def test_staff_can_add_multiple_documents_with_metadata(self):
        f1 = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 receipt', content_type='application/pdf')
        f2 = SimpleUploadedFile('photo.jpg', b'\xff\xd8\xff jpeg', content_type='image/jpeg')
        resp = self.client.post(self.add_url, {'documents': [f1, f2]})
        self.assertRedirects(resp, self.edit_url)
        self.assertEqual(self.piece.documents.count(), 2)
        doc = self.piece.documents.get(original_name='receipt.pdf')
        self.assertEqual(doc.content_type, 'application/pdf')
        self.assertEqual(doc.size, len(b'%PDF-1.4 receipt'))
        self.assertIsNotNone(doc.uploaded_by)

    @override_settings(MAX_DOCUMENT_UPLOAD_BYTES=5)
    def test_oversized_document_is_rejected(self):
        f = SimpleUploadedFile('big.pdf', b'%PDF-1.4 too big', content_type='application/pdf')
        self.client.post(self.add_url, {'documents': [f]})
        self.assertEqual(self.piece.documents.count(), 0)

    def test_disallowed_type_is_rejected(self):
        f = SimpleUploadedFile('script.exe', b'MZ binary', content_type='application/octet-stream')
        self.client.post(self.add_url, {'documents': [f]})
        self.assertEqual(self.piece.documents.count(), 0)

    def test_a_valid_file_survives_a_rejected_sibling(self):
        good = SimpleUploadedFile('ok.pdf', b'%PDF ok', content_type='application/pdf')
        bad = SimpleUploadedFile('bad.exe', b'MZ', content_type='application/octet-stream')
        self.client.post(self.add_url, {'documents': [good, bad]})
        self.assertEqual(self.piece.documents.count(), 1)
        self.assertEqual(self.piece.documents.get().original_name, 'ok.pdf')


class DocumentDeleteTests(TestCase):
    def test_staff_can_delete_a_document(self):
        piece = make_piece()
        doc = make_document(piece)
        name, storage = doc.file.name, doc.file.storage
        self.client.force_login(make_staff())
        resp = self.client.post(reverse('art:curate:document-delete', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertRedirects(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertEqual(PieceDocument.objects.count(), 0)
        self.assertFalse(storage.exists(name))  # file removed by the post_delete signal


class DocumentThumbnailTests(TestCase):
    def test_uploading_a_real_image_generates_a_thumbnail(self):
        piece = make_piece()
        self.client.force_login(make_staff())
        self.client.post(
            reverse('art:curate:document-add', kwargs={'slug': piece.slug}),
            {'documents': [tiny_png('photo.png')]},
        )
        doc = piece.documents.get()
        self.assertTrue(doc.thumbnail)
        self.assertTrue(doc.thumbnail.storage.exists(doc.thumbnail.name))

    def test_backfill_command_generates_missing_thumbnails(self):
        doc = make_document(make_piece(), name='photo.png', content=tiny_png().read())
        self.assertFalse(doc.thumbnail)  # made directly, no thumbnail yet
        call_command('backfill_document_thumbnails', stdout=StringIO())
        doc.refresh_from_db()
        self.assertTrue(doc.thumbnail)

    def test_backfill_skips_existing_and_tolerates_unrenderable(self):
        piece = make_piece()
        done = make_document(piece, name='done.png', content=tiny_png().read())
        done.generate_thumbnail()
        done_thumb = done.thumbnail.name
        todo = make_document(piece, name='todo.png', content=tiny_png().read())
        bad = make_document(piece, name='bad.pdf', content=b'not a pdf', content_type='application/pdf')
        call_command('backfill_document_thumbnails', stdout=StringIO())
        for d in (done, todo, bad):
            d.refresh_from_db()
        self.assertEqual(done.thumbnail.name, done_thumb)  # already had one → left untouched
        self.assertTrue(todo.thumbnail)  # missing one → generated
        self.assertFalse(bad.thumbnail)  # unrenderable → still none, run completed

    def test_staff_can_view_the_thumbnail_bytes(self):
        piece = make_piece()
        doc = make_document(piece, name='photo.png', content=tiny_png().read())
        doc.generate_thumbnail()
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:curate:document-thumb', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/jpeg')
        self.assertTrue(b''.join(resp.streaming_content).startswith(b'\xff\xd8'))  # JPEG magic

    def test_thumbnail_view_404s_when_there_is_no_thumbnail(self):
        piece = make_piece()
        doc = make_document(piece)  # default fake content → no thumbnail
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:curate:document-thumb', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertEqual(resp.status_code, 404)


class DocumentEditPageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_staff())

    def test_edit_page_lists_documents_with_links_and_upload_form(self):
        piece = make_piece()
        doc = make_document(piece, name='receipt.pdf')
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'Documents')
        self.assertContains(resp, 'receipt.pdf')
        self.assertNotContains(resp, '{#')  # no unrendered (multi-line) template comment
        self.assertContains(resp, reverse('art:curate:document-download', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertContains(resp, reverse('art:curate:document-add', kwargs={'slug': piece.slug}))
        self.assertContains(resp, reverse('art:curate:document-delete', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        # Controls sit inside the piece form but submit standalone forms via form=,
        # so "Save changes" never picks up the document file input.
        self.assertContains(resp, 'id="doc-add-form"')
        self.assertContains(resp, 'form="doc-add-form"')
        self.assertContains(resp, f'id="doc-del-{doc.pk}"')
        self.assertContains(resp, f'form="doc-del-{doc.pk}"')

    def test_add_page_prompts_to_save_the_piece_first(self):
        resp = self.client.get(reverse('art:curate:piece-add'))
        self.assertContains(resp, 'Save the piece first')

    def test_edit_page_shows_a_thumbnail_img_when_available(self):
        piece = make_piece()
        doc = make_document(piece, name='photo.png', content=tiny_png().read())
        doc.generate_thumbnail()
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertContains(resp, reverse('art:curate:document-thumb', kwargs={'slug': piece.slug, 'pk': doc.pk}))

    def test_edit_page_shows_the_extension_fallback_without_a_thumbnail(self):
        piece = make_piece()
        make_document(piece, name='receipt.pdf')  # fake content → no thumbnail
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'curate-documents__ext')  # the fallback tile
        self.assertContains(resp, '>PDF<')  # the uppercase extension label

    def test_saving_the_piece_ignores_a_stray_documents_upload(self):
        # The document inputs are associated (via form=) with separate forms, so
        # posting a 'documents' part to the piece form must NOT create documents.
        piece = make_piece()
        resp = self.client.post(
            reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}),
            {
                'title': piece.title,
                'artist': str(piece.artist_id),
                'location': str(piece.location_id),
                'documents': tiny_png('sneaky.png'),
            },
        )
        self.assertEqual(resp.status_code, 302)  # piece saved
        self.assertEqual(piece.documents.count(), 0)  # the stray upload was ignored
