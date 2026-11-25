import json
import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from request_board.models import Request, RequestOffer
from request_board.templatetags.request_board_assets import ASSET_VERSION

User = get_user_model()


class T05TemplateContractTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="t05_owner", password="Password123!")
        self.donor = User.objects.create_user(username="t05_donor", password="Password123!")
        self.viewer = User.objects.create_user(username="t05_viewer", password="Password123!")
        self.item = Request.objects.create(
            owner=self.owner,
            title="Butuh Sayuran Segar",
            food_item="Bayam",
            quantity=Decimal("2.50"),
            unit="kg",
            location="Depok",
            expires_at=timezone.now() + timedelta(hours=12),
        )
        self.offer = RequestOffer.objects.create(request=self.item, offered_by=self.donor)
        self.item.status = Request.STATUS_OFFERED
        self.item.save(update_fields=["status", "updated_at"])

    def test_asset_version_bumped_and_embedded(self):
        self.assertNotEqual(ASSET_VERSION, "20261029-create-retry-retention")
        res = self.client.get(reverse("request_board:request_board"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, f"request-board.js?v={ASSET_VERSION}")

    def test_ensure_csrf_cookie_on_board_create_and_offers_views(self):
        guest = Client()
        guest_board = guest.get(reverse("request_board:request_board"))
        self.assertEqual(guest_board.status_code, 200)
        self.assertIn("csrftoken", guest_board.cookies)

        owner_client = Client()
        owner_client.force_login(self.owner)
        create_res = owner_client.get(reverse("request_board:request_create"))
        self.assertEqual(create_res.status_code, 200)
        self.assertIn("csrftoken", create_res.cookies)

        offers_res = owner_client.get(reverse("request_board:request_offers", args=[self.item.pk]))
        self.assertEqual(offers_res.status_code, 200)
        self.assertIn("csrftoken", offers_res.cookies)

    def test_index_contains_reversed_api_v1_attributes_and_preserves_html_fallbacks(self):
        client = Client()
        client.force_login(self.viewer)
        res = client.get(reverse("request_board:request_board"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn(f'data-api-requests-url="{reverse("request_board_api:request_list")}"', html)
        self.assertIn(
            f'data-api-detail-template="{reverse("request_board_api:request_detail", args=[111111111])}"',
            html,
        )
        self.assertIn(
            f'data-api-offers-template="{reverse("request_board_api:request_offer_list", args=[111111111])}"',
            html,
        )
        self.assertIn(
            f'data-api-selection-template="{reverse("request_board_api:request_selection", args=[111111111])}"',
            html,
        )
        self.assertIn(
            f'data-api-status-template="{reverse("request_board_api:request_status", args=[111111111])}"',
            html,
        )
        self.assertIn(
            f'data-api-creation-status-template="{reverse("request_board_api:request_creation_status", args=["00000000-0000-0000-0000-000000000000"])}"',
            html,
        )
        self.assertIn(f'data-request-id="{self.item.pk}"', html)
        self.assertIn(f'action="{reverse("request_board:request_offer", args=[self.item.pk])}"', html)

    def test_create_and_offers_templates_expose_api_v1_urls_and_fallbacks(self):
        client = Client()
        client.force_login(self.owner)

        form_html = client.get(reverse("request_board:request_create")).content.decode("utf-8")
        self.assertIn(f'data-api-requests-url="{reverse("request_board_api:request_list")}"', form_html)
        self.assertIn(
            f'data-api-creation-status-template="{reverse("request_board_api:request_creation_status", args=["00000000-0000-0000-0000-000000000000"])}"',
            form_html,
        )
        self.assertIn(f'action="{reverse("request_board:request_create")}"', form_html)

        offers_html = client.get(reverse("request_board:request_offers", args=[self.item.pk])).content.decode("utf-8")
        self.assertIn(
            f'data-api-detail-url="{reverse("request_board_api:request_detail", args=[self.item.pk])}"',
            offers_html,
        )
        self.assertIn(
            f'data-api-offers-url="{reverse("request_board_api:request_offer_list", args=[self.item.pk])}"',
            offers_html,
        )
        self.assertIn(
            f'data-api-offer-detail-template="{reverse("request_board_api:request_offer_detail", args=[self.item.pk, 222222222])}"',
            offers_html,
        )
        self.assertIn(
            f'data-api-selection-url="{reverse("request_board_api:request_selection", args=[self.item.pk])}"',
            offers_html,
        )
        self.assertIn(f'data-offer-id="{self.offer.pk}"', offers_html)
        self.assertIn(
            f'action="{reverse("request_board:request_select_offer", args=[self.item.pk, self.offer.pk])}"',
            offers_html,
        )


NODE_JS_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scriptSource = fs.readFileSync(process.argv[2], 'utf8');

class FakeClassList {
  constructor(el) { this.el = el; this._set = new Set(); }
  _syncFromAttr(val) {
    this._set = new Set(String(val || '').split(/\s+/).filter(Boolean));
  }
  _flush() { this.el._className = [...this._set].join(' '); }
  add(...tokens) { tokens.forEach(t => t && this._set.add(t)); this._flush(); }
  remove(...tokens) { tokens.forEach(t => this._set.delete(t)); this._flush(); }
  toggle(token, force) {
    if (force === undefined) {
      if (this._set.has(token)) this._set.delete(token); else this._set.add(token);
    } else if (force) this._set.add(token);
    else this._set.delete(token);
    this._flush();
  }
  contains(token) { return this._set.has(token); }
}

function toCamel(attr) {
  return attr.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}
function toKebab(prop) {
  return 'data-' + prop.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
}

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.nodeType = 1;
    this.ownerDocument = ownerDocument;
    this.parentElement = null;
    this.parentNode = null;
    this.childNodes = [];
    this.attributes = new Map();
    this._className = '';
    this.classList = new FakeClassList(this);
    this._listeners = new Map();
    this.dataset = new Proxy({}, {
      get: (_, p) => this.attributes.get(toKebab(String(p))),
      set: (_, p, v) => { this.setAttribute(toKebab(String(p)), String(v)); return true; },
      deleteProperty: (_, p) => { this.attributes.delete(toKebab(String(p))); return true; },
    });
    this.hidden = false;
    this.disabled = false;
    this.inert = false;
    this.open = false;
    this.value = '';
    this.name = '';
    this.type = '';
    this.required = false;
    this.id = '';
    this.tabIndex = 0;
    this.focused = false;
  }
  get className() { return this._className; }
  set className(val) { this._className = String(val || ''); this.classList._syncFromAttr(this._className); }
  get textContent() {
    return this.childNodes.map(c => c.textContent).join('');
  }
  set textContent(val) {
    this.childNodes = [];
    if (val !== null && val !== undefined && val !== '') {
      this.append(this.ownerDocument.createTextNode(String(val)));
    }
  }
  get isConnected() {
    let cur = this;
    while (cur) {
      if (cur === this.ownerDocument.body) return true;
      cur = cur.parentElement;
    }
    return false;
  }
  setAttribute(name, val) {
    const str = String(val);
    this.attributes.set(name, str);
    if (name === 'id') this.id = str;
    if (name === 'class') this.className = str;
    if (name === 'name') this.name = str;
    if (name === 'type') this.type = str;
    if (name === 'hidden') this.hidden = true;
  }
  getAttribute(name) {
    if (name === 'id') return this.id;
    if (name === 'class') return this._className;
    if (name === 'name') return this.name || this.attributes.get('name') || null;
    if (name === 'type') return this.type || this.attributes.get('type') || null;
    if (name === 'href') return this.href ?? this.attributes.get('href') ?? null;
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }
  hasAttribute(name) {
    if (name === 'hidden') return this.hidden || this.attributes.has('hidden');
    return this.attributes.has(name);
  }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === 'hidden') this.hidden = false;
  }
  append(...nodes) {
    for (const n of nodes) {
      const node = typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n;
      if (node.tagName === '#fragment') {
        this.append(...[...node.childNodes]);
        continue;
      }
      if (node.parentNode) node.remove();
      node.parentNode = this;
      node.parentElement = this;
      this.childNodes.push(node);
    }
  }
  prepend(...nodes) {
    const resolved = [];
    for (const n of nodes) {
      const node = typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n;
      if (node.tagName === '#fragment') resolved.push(...node.childNodes);
      else resolved.push(node);
    }
    for (const node of resolved.reverse()) {
      if (node.parentNode) node.remove();
      node.parentNode = this;
      node.parentElement = this;
      this.childNodes.unshift(node);
    }
  }
  replaceChildren(...nodes) {
    this.childNodes.forEach(c => { c.parentNode = null; c.parentElement = null; });
    this.childNodes = [];
    this.append(...nodes);
  }
  before(...nodes) {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx < 0) return;
    const list = nodes.map(n => typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n);
    list.forEach(n => { n.parentNode = this.parentNode; n.parentElement = this.parentNode; });
    this.parentNode.childNodes.splice(idx, 0, ...list);
  }
  after(...nodes) {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx < 0) return;
    const list = nodes.map(n => typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n);
    list.forEach(n => { n.parentNode = this.parentNode; n.parentElement = this.parentNode; });
    this.parentNode.childNodes.splice(idx + 1, 0, ...list);
  }
  replaceWith(...nodes) {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx < 0) return;
    const list = nodes.map(n => typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n);
    list.forEach(n => { n.parentNode = this.parentNode; n.parentElement = this.parentNode; });
    this.parentNode.childNodes.splice(idx, 1, ...list);
    this.parentNode = null;
    this.parentElement = null;
  }
  remove() {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx >= 0) this.parentNode.childNodes.splice(idx, 1);
    this.parentNode = null;
    this.parentElement = null;
  }
  focus() { this.focused = true; this.ownerDocument.activeElement = this; }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  dispatchEvent(evt) {
    evt.target = evt.target || this;
    evt.currentTarget = this;
    const list = this._listeners.get(evt.type) || [];
    for (const fn of list) fn.call(this, evt);
    if (evt.bubbles !== false && this.parentElement) {
      this.parentElement.dispatchEvent(evt);
    }
    return !evt.defaultPrevented;
  }
  matches(selector) {
    return selector.split(',').map(s => s.trim()).some(part => matchCompound(this, part));
  }
  closest(selector) {
    let cur = this;
    while (cur && cur.nodeType === 1) {
      if (cur.matches(selector)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const child of node.childNodes || []) {
        if (child.nodeType === 1) {
          if (child.matches(selector)) out.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  contains(other) {
    let cur = other;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentElement;
    }
    return false;
  }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatchEvent({ type: 'close', bubbles: false, preventDefault() {} }); }
  reset() {
    for (const el of this.querySelectorAll('input, select, textarea')) {
      if (el.type !== 'hidden') el.value = el._defaultValue ?? '';
    }
  }
  get elements() {
    const els = this.querySelectorAll('input, select, textarea, button');
    const obj = [...els];
    obj.namedItem = (name) => els.find(e => e.name === name) || null;
    for (const e of els) {
      if (e.name && !(e.name in obj)) obj[e.name] = e;
    }
    return obj;
  }
}

function matchCompound(el, chain) {
  const parts = chain.trim().split(/\s+/).filter(Boolean);
  if (!parts.length || !matchSingle(el, parts[parts.length - 1])) return false;
  let cur = el.parentElement;
  for (let i = parts.length - 2; i >= 0; i -= 1) {
    while (cur && !matchSingle(cur, parts[i])) cur = cur.parentElement;
    if (!cur) return false;
    cur = cur.parentElement;
  }
  return true;
}

function matchSingle(el, sel) {
  if (!sel) return false;
  // Supports compound tag, .class, #id, [attr], [attr=val]
  let rest = sel;
  const tagMatch = rest.match(/^[a-zA-Z0-9_-]+/);
  if (tagMatch) {
    if (el.tagName !== tagMatch[0].toUpperCase()) return false;
    rest = rest.slice(tagMatch[0].length);
  }
  for (const m of rest.matchAll(/\.([a-zA-Z0-9_-]+)/g)) {
    if (!el.classList.contains(m[1])) return false;
  }
  for (const m of rest.matchAll(/#([a-zA-Z0-9_-]+)/g)) {
    if (el.id !== m[1]) return false;
  }
  for (const m of rest.matchAll(/\[([^\]=]+)(?:=["']?([^"'\]]*)["']?)?\]/g)) {
    const attr = m[1];
    const expected = m[2];
    if (expected === undefined) {
      if (!el.hasAttribute(attr)) return false;
    } else {
      if (el.getAttribute(attr) !== expected) return false;
    }
  }
  if (sel.includes(':modal') && !el.open) return false;
  if (sel.includes(':not([type=hidden])') && el.type === 'hidden') return false;
  if (sel.includes(':not(:disabled)') && el.disabled) return false;
  return true;
}

class FakeTextNode {
  constructor(text) { this.nodeType = 3; this.textContent = text; this.parentNode = null; this.parentElement = null; }
  remove() {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx >= 0) this.parentNode.childNodes.splice(idx, 1);
    this.parentNode = null; this.parentElement = null;
  }
}
class FakeCommentNode {
  constructor(text) { this.nodeType = 8; this.textContent = text; this.parentNode = null; this.parentElement = null; }
  replaceWith(node) {
    if (!this.parentNode) return;
    const idx = this.parentNode.childNodes.indexOf(this);
    if (idx >= 0) {
      node.parentNode = this.parentNode; node.parentElement = this.parentNode;
      this.parentNode.childNodes.splice(idx, 1, node);
    }
  }
}

function makeEnv(fetchImpl, setupPage) {
  const docListeners = new Map();
  const winListeners = new Map();
  const doc = {
    cookie: 'csrftoken=cookie-csrf-token-123',
    hidden: false,
    activeElement: null,
    createElement(tag) {
      const el = new FakeElement(tag, doc);
      if (tag.toLowerCase() === 'form') {
        Object.defineProperty(el, 'tagName', { value: 'FORM' });
      }
      return el;
    },
    createTextNode(t) { return new FakeTextNode(t); },
    createComment(t) { return new FakeCommentNode(t); },
    createDocumentFragment() { return new FakeElement('#fragment', doc); },
    getElementById(id) { return doc.body.querySelector(`#${id}`); },
    querySelector(sel) { return doc.body.querySelector(sel); },
    querySelectorAll(sel) { return doc.body.querySelectorAll(sel); },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    dispatchEvent(evt) {
      const list = docListeners.get(evt.type) || [];
      for (const fn of list) fn.call(doc, evt);
    },
  };
  doc.body = new FakeElement('body', doc);
  doc.body.dispatchEvent = (evt) => {
    FakeElement.prototype.dispatchEvent.call(doc.body, evt);
    doc.dispatchEvent(evt);
  };

  const toasts = [];
  const historyPush = [];
  const win = {
    location: new URL('http://testserver/board/'),
    history: {
      pushState: (_, __, url) => {
        historyPush.push(url);
        win.location = new URL(url, win.location.href);
      },
    },
    crypto: require('node:crypto').webcrypto,
    fetch: fetchImpl,
    AbortController,
    URL,
    URLSearchParams,
    FormData: class FakeFormData {
      constructor(form) {
        this._entries = [];
        for (const el of form.elements) {
          if (el.name && !el.disabled) this._entries.push([el.name, el.value]);
        }
      }
      entries() { return this._entries[Symbol.iterator](); }
    },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    DOMParser: class { parseFromString() { return doc; } },
    navigator: { onLine: true },
    performance,
    setTimeout,
    clearTimeout,
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
    showRequestBoardToast(message, isError, loginUrl) {
      toasts.push({ message, isError, loginUrl });
      const t = doc.createElement('div');
      t.textContent = message;
      return t;
    },
  };
  win.HTMLFormElement = FakeElement;
  win.HTMLDialogElement = FakeElement;

  setupPage(doc, win);

  const sandbox = {
    window: win,
    document: doc,
    navigator: win.navigator,
    performance: win.performance,
    crypto: win.crypto,
    fetch: fetchImpl,
    AbortController,
    URL,
    URLSearchParams,
    FormData: win.FormData,
    CustomEvent: win.CustomEvent,
    DOMParser: win.DOMParser,
    HTMLFormElement: FakeElement,
    HTMLDialogElement: FakeElement,
    Node: { TEXT_NODE: 3 },
    DOMException,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(scriptSource, sandbox);
  return { doc, win, toasts, historyPush };
}

function buildBoardRoot(doc, { userId = '10', userName = 'owner_u' } = {}) {
  const root = doc.createElement('div');
  root.className = 'request-board';
  root.setAttribute('data-board-root', '');
  root.setAttribute('data-current-user-id', userId);
  root.setAttribute('data-login-url', '/login/');
  root.setAttribute('data-board-url', '/board/');
  root.setAttribute('data-create-url', '/board/new/');
  root.setAttribute('data-api-requests-url', '/api/v1/requests/');
  root.setAttribute('data-api-detail-template', '/api/v1/requests/111111111/');
  root.setAttribute('data-api-offers-template', '/api/v1/requests/111111111/offers/');
  root.setAttribute('data-api-offer-detail-template', '/api/v1/requests/111111111/offers/222222222/');
  root.setAttribute('data-api-selection-template', '/api/v1/requests/111111111/selection/');
  root.setAttribute('data-api-status-template', '/api/v1/requests/111111111/status/');
  root.setAttribute('data-api-creation-status-template', '/api/v1/request-creations/00000000-0000-0000-0000-000000000000/');
  root.setAttribute('data-offers-page-template', '/board/111111111/offers/');
  root.setAttribute('data-offer-fallback-template', '/board/111111111/offer/');
  root.setAttribute('data-fulfill-fallback-template', '/board/111111111/fulfill/');
  root.setAttribute('data-delete-fallback-template', '/board/111111111/delete/');
  root.setAttribute('data-static-icon-base', '/static/request-board/');

  const filterForm = doc.createElement('form');
  filterForm.setAttribute('data-filter-form', '');
  filterForm.action = 'http://testserver/board/';
  for (const name of ['title', 'q', 'food_item', 'status', 'location', 'sort', 'mine']) {
    const inp = doc.createElement('input');
    inp.name = name;
    inp.value = name === 'sort' ? 'newest' : '';
    filterForm.append(inp);
  }
  const results = doc.createElement('div');
  results.setAttribute('data-board-results', '');
  const status = doc.createElement('div');
  status.setAttribute('data-board-status', '');
  root.append(filterForm, status, results);
  doc.body.append(root);
  return { root, filterForm, results, status };
}

async function runTests() {
  // 1. Stale filter response guard + XSS escaping + private identity absence for guest
  {
    let resolveSlow;
    const calls = [];
    const { doc, win } = makeEnv(
      (url, opts) => {
        calls.push({ url: String(url), opts });
        if (String(url).includes('title=slow')) {
          return new Promise((res) => {
            resolveSlow = () => res(new Response(JSON.stringify({
              count: 1, page: 1, page_size: 12, total_pages: 1,
              results: [{
                id: 99, owner: { id: 2, display_name: 'OldOwner' },
                title: 'Old Stale Result', food_item: 'Roti', quantity: '1.00', unit: 'kg',
                description: '', location: '', status: 'pending', effective_status: 'pending',
                expires_at: null, offer_count: 1, has_offered: false, selected_offer_id: null,
                created_at: '2026-11-14T10:00:00+07:00',
              }],
            }), { status: 200 }));
          });
        }
        return Promise.resolve(new Response(JSON.stringify({
          count: 1, page: 1, page_size: 12, total_pages: 1,
          results: [{
            id: 100, owner: { id: 2, display_name: '<img src=x onerror=alert(1)>' },
            title: '<script>window.__xss=1</script>', food_item: 'Nasi', quantity: '2.50', unit: 'porsi',
            description: '<b>raw</b>', location: 'Depok', status: 'offered', effective_status: 'offered',
            expires_at: '2026-11-15T10:00:00+07:00', offer_count: 3, has_offered: false, selected_offer_id: null,
            created_at: '2026-11-14T10:00:00+07:00',
          }],
        }), { status: 200 }));
      },
      (doc) => buildBoardRoot(doc, { userId: '' }),
    );
    const filterForm = doc.querySelector('[data-filter-form]');
    filterForm.elements.namedItem('title').value = 'slow';
    filterForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 10));

    filterForm.elements.namedItem('title').value = 'fast';
    filterForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 20));
    resolveSlow();
    await new Promise(r => setTimeout(r, 20));

    const results = doc.querySelector('[data-board-results]');
    assert.equal(results.querySelectorAll('.request-board__card').length, 1);
    const titleEl = results.querySelector('.request-board__card-title');
    assert.equal(titleEl.textContent, '<script>window.__xss=1</script>');
    assert.equal(results.querySelectorAll('script').length, 0);
    assert.equal(calls.some(c => c.url.includes('/offers/')), false, 'Guest must never call offers endpoint');
  }

  // 2. Idempotent Create: timeout -> same Idempotency-Key + same JSON payload -> status 404 doesn't auto-create -> retry succeeds
  {
    const postCalls = [];
    let attemptNum = 0;
    const { doc, win } = makeEnv(
      (url, opts) => {
        const u = String(url);
        const m = (opts?.method || 'GET').toUpperCase();
        if (m === 'POST' && u.includes('/api/v1/requests/')) {
          attemptNum += 1;
          postCalls.push({
            key: opts.headers['Idempotency-Key'],
            csrf: opts.headers['X-CSRFToken'],
            ct: opts.headers['Content-Type'],
            accept: opts.headers['Accept'],
            creds: opts.credentials,
            body: opts.body,
          });
          if (attemptNum === 1) {
            return Promise.reject(new TypeError('network timeout'));
          }
          return Promise.resolve(new Response(JSON.stringify({
            id: 501, owner: { id: 10, display_name: 'owner_u' },
            title: 'Beras 5kg', food_item: 'Beras', quantity: '5.00', unit: 'kg',
            description: '', location: '', status: 'pending', effective_status: 'pending',
            expires_at: '2026-11-15T19:00:00+07:00', offer_count: 0, has_offered: false, selected_offer_id: null,
            created_at: '2026-11-14T19:00:00+07:00',
          }), { status: 201 }));
        }
        if (u.includes('/api/v1/request-creations/')) {
          return Promise.resolve(new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 }));
        }
        return Promise.resolve(new Response(JSON.stringify({ count: 0, page: 1, page_size: 12, total_pages: 1, results: [] }), { status: 200 }));
      },
      (doc) => {
        const { root } = buildBoardRoot(doc, { userId: '10' });
        const form = doc.createElement('form');
        form.setAttribute('data-board-create', '');
        form.action = 'http://testserver/board/new/';
        for (const [n, v, req] of [
          ['title', 'Beras 5kg', true], ['food_item', 'Beras', true], ['quantity', '5', true],
          ['unit', 'kg', true], ['expires_at', '2027-01-01T12:00', true],
          ['location', '', false], ['description', '', false],
        ]) {
          const el = doc.createElement('input');
          el.name = n; el.value = v; el.required = req;
          form.append(el);
        }
        const btn = doc.createElement('button');
        btn.type = 'submit';
        btn.textContent = 'Simpan Permintaan';
        form.append(btn);
        root.append(form);
      },
    );
    const form = doc.querySelector('[data-board-create]');
    form.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 40));

    assert.equal(postCalls.length, 1);
    assert.equal(postCalls[0].csrf, 'cookie-csrf-token-123');
    assert.equal(postCalls[0].ct, 'application/json');
    assert.equal(postCalls[0].accept, 'application/json');
    assert.equal(postCalls[0].creds, 'same-origin');
    const parsedBody = JSON.parse(postCalls[0].body);
    assert.equal(parsedBody.expires_at, '2027-01-01T12:00+07:00');
    const recoveryBtn = doc.querySelector('[data-create-recovery] button');
    assert.ok(recoveryBtn, 'Recovery Periksa status button must be present');

    // Click Periksa status -> 404 must NOT auto-create another POST
    recoveryBtn.dispatchEvent({ type: 'click', bubbles: true, preventDefault() {} });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(postCalls.length, 1, '404 on creation status must not auto-create a new POST');
    assert.equal(form.querySelector('button[type=submit]').disabled, false);

    // Manual retry -> MUST send exact same Idempotency-Key & exact same body
    form.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(postCalls.length, 2);
    assert.equal(postCalls[1].key, postCalls[0].key);
    assert.equal(postCalls[1].body, postCalls[0].body);
  }

  // 3. Delete 204 empty body + Fulfill 409 rollback & refresh + Offer {} payload
  {
    const reqLog = [];
    const { doc, toasts } = makeEnv(
      (url, opts) => {
        const u = String(url);
        const m = (opts?.method || 'GET').toUpperCase();
        reqLog.push({ method: m, url: u, body: opts?.body });
        if (m === 'DELETE' && u.endsWith('/api/v1/requests/77/')) {
          return Promise.resolve(new Response(null, { status: 204 }));
        }
        if (m === 'PATCH' && u.endsWith('/api/v1/requests/78/status/')) {
          return Promise.resolve(new Response(JSON.stringify({ detail: 'Permintaan kedaluwarsa.', code: 'request_expired' }), { status: 409 }));
        }
        if (m === 'POST' && u.endsWith('/api/v1/requests/79/offers/')) {
          return Promise.resolve(new Response(JSON.stringify({ id: 9, request_id: 79, offered_by: { id: 10, display_name: 'owner_u' }, created_at: '2026-11-14T19:00:00+07:00', is_selected: false }), { status: 201 }));
        }
        return Promise.resolve(new Response(JSON.stringify({ count: 0, page: 1, page_size: 12, total_pages: 1, results: [] }), { status: 200 }));
      },
      (doc) => {
        const { results } = buildBoardRoot(doc, { userId: '10' });
        const mkCard = (id, action, actionUrl) => {
          const card = doc.createElement('article');
          card.className = 'request-board__card request-board__card--offered';
          card.setAttribute('data-request-id', String(id));
          const badge = doc.createElement('span');
          badge.className = 'request-board__badge request-board__badge--offered';
          badge.append(doc.createTextNode(' Ditawarkan'));
          const count = doc.createElement('span');
          count.setAttribute('data-offer-count', '1');
          count.textContent = '1 penawaran';
          const actions = doc.createElement('div');
          actions.className = 'request-board__owner-actions';
          const form = doc.createElement('form');
          form.action = actionUrl;
          form.setAttribute('data-card-action', action);
          const btn = doc.createElement('button');
          btn.type = 'submit';
          btn.textContent = action;
          form.append(btn);
          actions.append(form);
          card.append(badge, count, actions);
          results.append(card);
          return form;
        };
        mkCard(77, 'delete', 'http://testserver/board/77/delete/');
        mkCard(78, 'fulfill', 'http://testserver/board/78/fulfill/');
        mkCard(79, 'offer', 'http://testserver/board/79/offer/');
      },
    );

    // Fulfill 409 conflict -> rollback & re-enable & reload board
    const fulfillForm = doc.querySelector('[data-card-action="fulfill"]');
    fulfillForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(reqLog.some(r => r.method === 'PATCH' && r.url.endsWith('/api/v1/requests/78/status/') && r.body === '{"status":"fulfilled"}'), true);
    assert.equal(fulfillForm.querySelector('button').disabled, false, 'Button must re-enable on 409 error');
    assert.equal(toasts.some(t => t.isError && t.message.includes('kedaluwarsa')), true);

    // Re-attach cards for 204 Delete and Offer check since boardGet refreshed DOM to empty
    const { results: r2 } = { results: doc.querySelector('[data-board-results]') };
    const mkForm = (id, action) => {
      const card = doc.createElement('article');
      card.className = 'request-board__card request-board__card--pending';
      card.setAttribute('data-request-id', String(id));
      const count = doc.createElement('span');
      count.setAttribute('data-offer-count', '0');
      count.textContent = '0 penawaran';
      const form = doc.createElement('form');
      form.action = `http://testserver/board/${id}/${action}/`;
      form.setAttribute('data-card-action', action);
      const btn = doc.createElement('button');
      btn.type = 'submit'; btn.textContent = action;
      form.append(btn); card.append(count, form); r2.append(card);
      return form;
    };
    const delForm = mkForm(77, 'delete');
    const offForm = mkForm(79, 'offer');

    delForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    const delReq = reqLog.find(r => r.method === 'DELETE' && r.url.endsWith('/api/v1/requests/77/'));
    assert.ok(delReq);
    assert.equal(delReq.body, undefined, 'DELETE must not send JSON body');
    assert.equal(toasts.some(t => !t.isError && t.message.includes('berhasil dihapus')), true);

    r2.append(offForm.closest('.request-board__card'));
    offForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(reqLog.some(r => r.method === 'POST' && r.url.endsWith('/api/v1/requests/79/offers/') && r.body === '{}'), true);
  }

  // 4. Offers page: selection PATCH + pagination GET without reload + 409 rollback + XSS escaping
  {
    const offerCalls = [];
    let selectedOfferId = null;
    const { doc, toasts, historyPush } = makeEnv(
      (url, opts) => {
        const u = String(url);
        const m = (opts?.method || 'GET').toUpperCase();
        offerCalls.push({ method: m, url: u, body: opts?.body });
        if (m === 'PATCH' && u.endsWith('/api/v1/requests/10/selection/')) {
          const parsed = JSON.parse(opts.body);
          if (parsed.offer_id === 999) {
            return Promise.resolve(new Response(JSON.stringify({ detail: 'Permintaan sudah terpenuhi.', code: 'request_fulfilled' }), { status: 409 }));
          }
          selectedOfferId = parsed.offer_id;
          return Promise.resolve(new Response(JSON.stringify({
            id: 10, owner: { id: 1, display_name: 'owner' },
            title: 'Butuh Gula', food_item: 'Gula', quantity: '1.00', unit: 'kg',
            description: '', location: '', status: 'offered', effective_status: 'offered',
            expires_at: null, offer_count: 2, has_offered: false, selected_offer_id: selectedOfferId,
            created_at: '2026-11-14T10:00:00+07:00',
          }), { status: 200 }));
        }
        if (m === 'GET' && u.endsWith('/api/v1/requests/10/offers/22/')) {
          return Promise.resolve(new Response(JSON.stringify({
            id: 22,
            request_id: 10,
            offered_by: { id: 4, display_name: 'DonorSatu' },
            created_at: '2026-11-14T11:00:00+07:00',
            is_selected: selectedOfferId === 22,
          }), { status: 200 }));
        }
        if (m === 'GET' && u.includes('/api/v1/requests/10/offers/')) {
          const page = new URL(u, 'http://testserver').searchParams.get('page') || '1';
          return Promise.resolve(new Response(JSON.stringify({
            count: 2, page: Number(page), page_size: 1, total_pages: 2,
            results: [{
              id: page === '2' ? 23 : 22,
              request_id: 10,
              offered_by: { id: 5, display_name: page === '2' ? '<b>Page2Donor</b>' : 'DonorSatu' },
              created_at: '2026-11-14T11:00:00+07:00',
              is_selected: selectedOfferId === (page === '2' ? 23 : 22),
            }],
          }), { status: 200 }));
        }
        if (m === 'GET' && u.endsWith('/api/v1/requests/10/')) {
          return Promise.resolve(new Response(JSON.stringify({
            id: 10, owner: { id: 1, display_name: 'owner' },
            title: 'Butuh Gula', food_item: 'Gula', quantity: '1.00', unit: 'kg',
            description: '', location: '', status: 'offered', effective_status: 'offered',
            expires_at: null, offer_count: 2, has_offered: false, selected_offer_id: selectedOfferId,
            created_at: '2026-11-14T10:00:00+07:00',
          }), { status: 200 }));
        }
        return Promise.resolve(new Response('{}', { status: 404 }));
      },
      (doc, win) => {
        win.location = new URL('http://testserver/board/10/offers/');
        const offersEl = doc.createElement('div');
        offersEl.className = 'request-board';
        offersEl.setAttribute('data-board-offers', '');
        offersEl.setAttribute('data-request-id', '10');
        offersEl.setAttribute('data-login-url', '/login/');
        offersEl.setAttribute('data-board-url', '/board/');
        offersEl.setAttribute('data-api-detail-url', '/api/v1/requests/10/');
        offersEl.setAttribute('data-api-offers-url', '/api/v1/requests/10/offers/');
        offersEl.setAttribute('data-api-offer-detail-template', '/api/v1/requests/10/offers/222222222/');
        offersEl.setAttribute('data-api-selection-url', '/api/v1/requests/10/selection/');
        offersEl.setAttribute('data-offer-select-fallback-template', '/board/10/offers/111111111/select/');
        offersEl.setAttribute('data-offer-user-icon', '/static/request-board/offer-user.svg');

        const listWrap = doc.createElement('section');
        listWrap.className = 'request-board__offers-list';
        const ul = doc.createElement('ul');
        ul.className = 'request-board__donors-list';

        const mkRow = (oid, name) => {
          const li = doc.createElement('li');
          li.className = 'request-board__donor';
          const dn = doc.createElement('span');
          dn.className = 'request-board__donor-name';
          dn.textContent = name;
          const f = doc.createElement('form');
          f.action = `http://testserver/board/10/offers/${oid}/select/`;
          f.setAttribute('data-offer-id', String(oid));
          const btn = doc.createElement('button');
          btn.type = 'submit'; btn.textContent = 'Pilih penawar';
          f.append(btn);
          li.append(dn, f);
          return { li, f, btn };
        };
        const rBad = mkRow(999, 'ConflictDonor');
        const rGood = mkRow(22, 'DonorSatu');
        ul.append(rBad.li, rGood.li);
        listWrap.append(ul);

        const nav = doc.createElement('nav');
        nav.className = 'request-board__offer-pagination';
        const nextLink = doc.createElement('a');
        nextLink.href = 'http://testserver/board/10/offers/?page=2';
        nextLink.textContent = 'Berikutnya';
        nav.append(nextLink);

        offersEl.append(listWrap, nav);
        doc.body.append(offersEl);
      },
    );

    // 409 rollback check
    const conflictForm = doc.querySelector('[data-offer-id="999"]');
    conflictForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 40));
    assert.equal(conflictForm.querySelector('button').disabled, false);
    assert.equal(toasts.some(t => t.isError && t.message.includes('terpenuhi')), true);

    // 200 selection PATCH + refreshOffers
    const goodForm = doc.querySelector('[data-offer-id="22"]');
    goodForm.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 40));
    assert.equal(offerCalls.some(c => c.method === 'PATCH' && c.body === '{"offer_id":22}'), true);
    assert.ok(doc.querySelector('[data-offer-selection]')?.textContent.includes('DonorSatu'));

    // Offer pagination without reload + XSS donor name rendered as plain text
    const nextAnchor = doc.querySelector('.request-board__offer-pagination a');
    nextAnchor.dispatchEvent({ type: 'click', button: 0, bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 40));
    assert.equal(historyPush.some(u => String(u).includes('?page=2')), true);
    assert.equal(doc.querySelector('.request-board__donor-name')?.textContent, '<b>Page2Donor</b>');
    assert.ok(
      doc.querySelector('[data-offer-selection]')?.textContent.includes('DonorSatu'),
      'Page-2 pagination must hydrate selected donor name via data-api-offer-detail-template',
    );
  }

  // 5. Create 400 field errors + 409 idempotency_deleted + 403 Auth login link
  {
    let mode = '400';
    const { doc, toasts } = makeEnv(
      (url, opts) => {
        if ((opts?.method || 'GET').toUpperCase() === 'POST') {
          if (mode === '400') {
            return Promise.resolve(new Response(JSON.stringify({ title: ['Judul tidak valid.'] }), { status: 400 }));
          }
          if (mode === '403') {
            return Promise.resolve(new Response(JSON.stringify({ detail: 'Autentikasi diperlukan untuk mengelola request.' }), { status: 403 }));
          }
          if (mode === '409del') {
            return Promise.resolve(new Response(JSON.stringify({ detail: 'Sudah dihapus.', code: 'idempotency_deleted' }), { status: 409 }));
          }
        }
        return Promise.resolve(new Response('{}', { status: 404 }));
      },
      (doc) => {
        const { root } = buildBoardRoot(doc, { userId: '10' });
        const form = doc.createElement('form');
        form.setAttribute('data-board-create', '');
        form.action = 'http://testserver/board/new/';
        for (const [n, v, req] of [
          ['title', 'Judul Valid', true], ['food_item', 'Roti', true], ['quantity', '2', true],
          ['unit', 'kg', true], ['expires_at', '2027-01-01T12:00', true],
        ]) {
          const el = doc.createElement('input');
          el.name = n; el.value = v; el.required = req;
          form.append(el);
        }
        const btn = doc.createElement('button');
        btn.type = 'submit'; btn.textContent = 'Simpan';
        form.append(btn);
        root.append(form);
      },
    );

    const form = doc.querySelector('[data-board-create]');
    form.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(form.elements.namedItem('title').getAttribute('aria-invalid'), 'true');
    assert.equal(form.querySelector('.request-board__field-errors')?.textContent, 'Judul tidak valid.');
    assert.equal(form.querySelector('button[type=submit]').disabled, false);

    mode = '403';
    form.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(
      toasts.some(t => t.isError && t.loginUrl && t.loginUrl.includes('/login/')),
      true,
      'Expired session 403 must produce an auth login link toast',
    );
    assert.equal(form.querySelector('button[type=submit]').disabled, false);

    mode = '409del';
    form.dispatchEvent({ type: 'submit', bubbles: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
    await new Promise(r => setTimeout(r, 30));
    assert.equal(form.querySelector('button[type=submit]').disabled, true, 'Deleted idempotency key must lock retry');
  }
}

runTests().catch((err) => { console.error(err); process.exit(1); });
"""


class T05JavaScriptHarnessTests(TestCase):
    def test_request_board_js_syntax_and_api_v1_behaviors(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js not available in PATH")
        js_path = Path(settings.BASE_DIR) / "static" / "js" / "request-board.js"
        syntax = subprocess.run([node, "--check", str(js_path)], capture_output=True, text=True, check=False)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        with tempfile.NamedTemporaryFile("w", suffix=".cjs", encoding="utf-8", delete=False) as tf:
            tf.write(NODE_JS_HARNESS)
            harness_path = tf.name
        try:
            run = subprocess.run(
                [node, harness_path, str(js_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, f"STDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}")
        finally:
            os.unlink(harness_path)
