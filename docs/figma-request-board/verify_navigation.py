"""Check the final landing-card navigation using production templates and Edge."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SaveBite.settings')
import django
django.setup()
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from playwright.sync_api import sync_playwright

response = Client().get('/', HTTP_HOST='localhost')
assert response.status_code == 200
landing = response.content.decode()
request = RequestFactory().get('/board/')
request.user = AnonymousUser()
board = render_to_string('request_board/index.html', {
    'user': request.user, 'page_obj': Paginator([], 20).get_page(1),
    'owner_offer_previews': {},
}, request=request)
report = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='msedge')
    context = browser.new_context()
    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith('/static/'):
            asset = ROOT / 'static' / path.removeprefix('/static/')
            route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or 'application/octet-stream')
        else:
            route.fulfill(body=board if path == '/board/' else landing, content_type='text/html')
    context.route('https://savebite.local/**', serve)
    try:
        for width in (320, 360, 375, 768, 769, 900, 1024, 1250, 1440):
            page = context.new_page()
            page.set_viewport_size({'width': width, 'height': 900})
            page.goto('https://savebite.local/')
            page.evaluate('document.fonts.ready')
            card = page.locator('article.feature').filter(has_text='Food Rescue Request Board')
            link = card.locator('a')
            assert link.count() == 1 and link.get_attribute('href') == '/board/'
            assert link.is_visible(), f'Request Board card link is hidden at {width}px'
            nav = page.locator('.navbar')
            page4 = nav.get_by_role('link', name='Page 4', exact=True, include_hidden=True)
            assert page4.count() == 1 and page4.get_attribute('href') == '#'
            assert nav.locator('a[href="/board/"]').count() == 0
            assert page.locator('.features .feature a[href="/board/"]').count() == 1
            bounds = link.bounding_box()
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
            if width <= 768:
                assert bounds['height'] >= 44
            if width in (375, 1440):
                card.screenshot(path=str(Path(__file__).parent / f'landing-request-card-{width}.png'))
            link.click()
            page.wait_for_url('**/board/')
            page.get_by_role('heading', name='Food Rescue Request Board', exact=True).wait_for(state='visible')
            if width <= 768:
                page.locator('#navToggle').click()
            assert page.get_by_role('link', name='Page 4', exact=True).is_visible()
            report.append({'width': width, 'landing_card_visible': True, 'destination': '/board/', 'navbar_page4_href': '#', 'link_bounds': bounds})
            page.close()
            print(f'PASS landing card navigation and Page 4 navbar at {width}px', flush=True)
    finally:
        browser.close()
(Path(__file__).parent / 'navigation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(f'PASS {len(report)} navigation/viewport combinations.')
