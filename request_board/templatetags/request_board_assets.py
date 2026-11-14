from django import template
from django.templatetags.static import static

register = template.Library()

# Bump once when releasing changes to the shared request board JS or CSS.
ASSET_VERSION = '20261114-rest-api-v2'


@register.simple_tag
def board_static(path):
    return f'{static(path)}?v={ASSET_VERSION}'
