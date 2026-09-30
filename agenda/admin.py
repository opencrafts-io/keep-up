from django.contrib import admin
from .models import Event

# Register your models here.
@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'summary',
        'start_time',
        'end_time',
        'status',
        'owner_id',
        'task_sync_status',
    )
    list_filter = ('status', 'all_day', 'transparency')
    search_fields = ('summary', 'description', 'location')
    readonly_fields = (
        'id',
        'owner_id',
        'created',
        'updated',
        'etag',
        'html_link',
        'task',
    )
    date_hierarchy = 'start_time'

    @admin.display(description='Google Tasks sync')
    def task_sync_status(self, event):
        if not event.task_id:
            return 'Not linked'
        return event.task.sync_status
    
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
        ('Google Tasks Mirror', {
            'fields': ('task',),
        }),
        ('Legacy Google Calendar Data', {
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
