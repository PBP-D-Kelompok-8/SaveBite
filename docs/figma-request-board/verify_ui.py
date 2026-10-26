"""Render production templates with synthetic data, without writing to the database.

Run: .venv/Scripts/python.exe docs/figma-request-board/verify_ui.py
Requires Playwright and installed Microsoft Edge. Outputs PNGs and a geometry report.
"""
import json
import argparse
import copy
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SaveBite.settings")
import django
django.setup()

from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.storage.base import Message
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory
from request_board.forms import RequestForm
from request_board.models import Request
from playwright.sync_api import sync_playwright

OUTPUT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--screens", nargs="+", default=["guest", "owner", "donor", "form", "invalid", "empty", "toasts", "long"])
parser.add_argument("--widths", nargs="+", type=int, default=[320, 360, 375, 768, 1024, 1440])
args = parser.parse_args()
owner = User(id=101, username="dapurberbagi")
donor = User(id=102, username="sayurbaik")
now = datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
rows = [
    ("Beras untuk dapur berbagi", "Beras", 5, "kg", "pending", "Beji, Depok",
     "Untuk menyiapkan makan siang bersama. Beras kemasan terbuka yang masih layak juga sangat membantu."),
    ("Sayuran untuk makan bersama", "Wortel dan kentang", 3, "kg", "offered", "Kukusan, Depok",
     "Kami membutuhkan sayuran segar untuk kegiatan masak bersama warga akhir pekan ini."),
    ("Roti untuk teman belajar", "Roti tawar", 10, "buah", "fulfilled", "Margonda, Depok",
     "Kebutuhan sudah terpenuhi. Terima kasih kepada semua yang telah menawarkan bantuan."),
    ("Minyak untuk dapur umum", "Minyak goreng", 2, "liter", "pending", "Beji, Depok",
     "Minyak goreng layak pakai untuk membantu persiapan makan malam di dapur umum."),
]
items = []
for index, (title, food, quantity, unit, status, location, description) in enumerate(rows, 1):
    item = Request(id=index, owner=owner, title=title, food_item=food, quantity=quantity,
                   unit=unit, status=status, location=location, description=description,
                   created_at=now, updated_at=now)
    item.offer_count = 2 if status != "pending" else 0
    item.has_offered = index == 2
    items.append(item)

def render(kind):
    user = owner if kind in ("owner", "long") else donor if kind in ("donor", "form", "invalid") else AnonymousUser()
    request = RequestFactory().get("/board/")
    request.user = user
    context = {"user": user, "messages": [], "owner_offer_previews": {}}
    if kind in ("form", "invalid"):
        template = "request_board/request_form.html"
        context["form"] = RequestForm(data={"title": "", "food_item": "Beras", "quantity": "0", "unit": "kg"}) if kind == "invalid" else RequestForm()
    else:
        template = "request_board/index.html"
        rendered_items = items
        if kind == "long":
            item = copy.copy(items[0])
            item.title = ("Beras dan bahan pokok untuk dapur berbagi komunitas peduli Depok " * 2)[:120]
            item.owner = User(id=101, username=("dapurberbagi_komunitas_peduli_depok_" * 5)[:150])
            item.location = "Kecamatan Beji, Kota Depok, Jawa Barat"
            item.description = "BahanMakananTanpaSpasi" * 20
            rendered_items = [item]
        context["page_obj"] = Paginator([] if kind == "empty" else rendered_items * 2, 4).get_page(1)
        if kind == "owner":
            context["owner_offer_previews"] = {2: {"items": [
                {"display_name": "sayurbaik", "created_at": now},
                {"display_name": "berbagirasa", "created_at": now},
            ], "has_more": False}}
    if kind == "toasts":
        context["messages"] = [Message(25, "Permintaan bantuan makanan berhasil dibuat."),
                               Message(30, "Anda sudah mengirimkan penawaran untuk permintaan ini sebelumnya."),
                               Message(40, "Permintaan sudah terpenuhi. Penawaran baru tidak dapat dikirim.")]
    return render_to_string(template, context, request=request)

report = []
screens = {kind: render(kind) for kind in args.screens}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge")
    browser_context = browser.new_context()
    font_cache = {}
    def cache_font(route):
        url = route.request.url
        if url not in font_cache:
            response = route.fetch()
            font_cache[url] = (response.body(), response.headers.get("content-type", "application/octet-stream"))
        body, content_type = font_cache[url]
        route.fulfill(body=body, content_type=content_type)
    browser_context.route("https://fonts.googleapis.com/**", cache_font)
    browser_context.route("https://fonts.gstatic.com/**", cache_font)
    try:
        for kind in args.screens:
            for width in args.widths:
                page = browser_context.new_page()
                page.set_viewport_size({"width": width, "height": 1000})
                if kind == "toasts":
                    page.clock.install(time="2030-01-01T00:00:00")
                    page.clock.pause_at("2030-01-01T00:00:01")
                html = screens[kind]
                def route_handler(route):
                    path = urlsplit(route.request.url).path
                    if path.startswith("/static/"):
                        asset = ROOT / "static" / path.removeprefix("/static/")
                        if asset.is_file():
                            route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
                        else:
                            route.fulfill(status=404, body="missing static asset")
                    else:
                        route.fulfill(body=html, content_type="text/html")
                page.route("https://savebite.local/**", route_handler)
                page.goto("https://savebite.local/board/")
                page.evaluate("document.fonts.ready")
                page.locator("img").evaluate_all("els => Promise.all(els.map(el => el.decode().catch(() => {})))")
                geometry = page.evaluate("""() => ({
                    width: innerWidth, documentWidth: document.documentElement.scrollWidth,
                    brokenImages: [...document.images].filter(i => !i.complete || !i.naturalWidth).map(i => i.src),
                    cards: [...document.querySelectorAll('.request-board__card')].map(i => ({x:i.offsetLeft, width:i.offsetWidth})),
                    assets: [...document.images].map(i => ({src:new URL(i.src).pathname, width:i.getBoundingClientRect().width, height:i.getBoundingClientRect().height}))
                })""")
                assert geometry["documentWidth"] <= width, (kind, width, geometry)
                assert not geometry["brokenImages"], (kind, width, geometry["brokenImages"])
                if width in (375, 1440):
                    page.screenshot(path=str(OUTPUT / f"{kind}-{width}.png"), full_page=True)
                if kind == "owner":
                    trigger = page.locator(".request-board__delete-summary").first
                    trigger.click()
                    dialog = page.locator("dialog[open]")
                    assert dialog.count() == 1
                    assert dialog.locator(".request-board__delete-cancel").evaluate("el => el === document.activeElement")
                    if width in (375, 1440):
                        page.screenshot(path=str(OUTPUT / f"delete-{width}.png"))
                    page.keyboard.press("Escape")
                    assert page.locator("dialog[open]").count() == 0
                    assert trigger.evaluate("el => el === document.activeElement")
                    trigger.click()
                    page.locator("dialog[open] .request-board__delete-cancel").click()
                    assert trigger.evaluate("el => el === document.activeElement")
                if width <= 768:
                    while page.locator('.request-board__toast-close').count():
                        page.locator('.request-board__toast-close').first.click()
                    toggle = page.locator("#navToggle")
                    toggle.click()
                    assert toggle.get_attribute("aria-expanded") == "true"
                report.append({"screen": kind, **geometry})
                print(f"Verified {kind} at {width}px", flush=True)
                page.close()
    finally:
        browser.close()
report_file = OUTPUT / "geometry.json"
previous = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else []
replaced = {(row["screen"], row["width"]) for row in report}
combined = [row for row in previous if (row["screen"], row["width"]) not in replaced] + report
report_file.write_text(json.dumps(combined, indent=2), encoding="utf-8")
print(f"Verified {len(report)} screen/viewport combinations: no overflow, all assets loaded; modal keyboard and mobile navigation passed.")
