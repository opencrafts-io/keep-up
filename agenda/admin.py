from django.contrib import admin
from .models import Event

# Register your models here.
@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('summary', 'start_time', 'end_time', 'status', 'owner_id')
    list_filter = ('status', 'all_day', 'transparency')
    search_fields = ('summary', 'description', 'location')
    readonly_fields = ('id', 'created', 'updated', 'etag', 'html_link')
    date_hierarchy = 'start_time'
    
    fieldsets = (
        ('Event Details', {
            'fields': ('summary', 'description', 'location')
        }),
        ('Time Information', {
            'fields': ('start_time', 'end_time', 'all_day', 'timezone')
        }),
        ('Event Metadata', {
            'fields': ('status', 'transparency')
        }),
        ('Google Calendar Info', {
            'fields': ('calendar_id', 'html_link', 'created', 'updated', 'etag'),
            'classes': ('collapse',)
        }),
        ('Additional Data', {
            'fields': ('attendees', 'reminders', 'recurrence'),
            'classes': ('collapse',)
        }),
        ('System Fields', {
            'fields': ('owner_id',),
            'classes': ('collapse',)
        }),
    )
