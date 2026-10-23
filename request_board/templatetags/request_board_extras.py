from django import template

register = template.Library()


@register.filter
def owner_preview(previews, request_id):
    """Lookup mapping preview milik actor; tanpa query, permission atau state."""
    return previews.get(request_id)
