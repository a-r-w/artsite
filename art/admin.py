from django.contrib import admin
from django.utils.html import format_html
from easy_thumbnails.files import get_thumbnailer

from .models import Artist, Location, Medium, Piece


@admin.register(Piece)
class PieceAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist', 'location', 'draft', 'tagged', 'admin_image_tag']
    list_filter = ['draft', 'artist', 'location', 'tagged', 'medium']
    search_fields = ['title', 'slug', 'artist__name', 'notes']
    readonly_fields = ['slug', 'admin_image_tag']
    list_select_related = ['artist', 'location', 'medium']

    @admin.display(description='Image')
    def admin_image_tag(self, obj):
        if not obj.image:
            return 'no image'
        thumb_url = get_thumbnailer(obj.image)['thumb'].url
        return format_html('<img src="{}" style="max-width:200px;max-height:200px">', thumb_url)

    fieldsets = (
        (None, {'fields': ('title', 'slug', 'image', 'admin_image_tag')}),
        (
            'Details',
            {'fields': ('artist', 'medium', 'medium_details', 'width', 'height', 'dimension_unit', 'location')},
        ),
        ('Acquisition', {'fields': ('date_acquired', 'acquired', 'purchase_price', 'purchase_currency', 'website')}),
        ('Notes', {'fields': ('notes', 'notes_private')}),
        ('Status', {'fields': ('draft', 'tagged')}),
    )


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'website']
    search_fields = ['name', 'email', 'notes']


@admin.register(Medium)
class MediumAdmin(admin.ModelAdmin):
    list_display = ['description']
    search_fields = ['description']


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['description']
    search_fields = ['description']
