/* Request Board REST API v1 integration & progressive enhancement.
 * Native <details>, <form method="post">, and <a href> remain HTML fallbacks.
 */
(function () {
  const DEFAULT_TIMEOUT_MS = 15000;
  const CREATE_RETRY_WINDOW_MS = 30 * 24 * 60 * 60 * 1000;
  const FILTER_DEBOUNCE_MS = 300;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const STATUS_LABELS = {
    pending: 'Pending',
    offered: 'Ditawarkan',
    fulfilled: 'Terpenuhi',
    expired: 'Kedaluwarsa',
  };
  const UNIT_LABELS = {
    gram: 'Gram',
    kg: 'Kg',
    ml: 'Ml',
    liter: 'Liter',
    buah: 'Buah',
    butir: 'Butir',
    ikat: 'Ikat',
    porsi: 'Porsi',
  };

  function getTimeoutMs() {
    const custom = Number(window.__REQUEST_BOARD_TIMEOUT_MS__);
    return Number.isFinite(custom) && custom > 0 ? custom : DEFAULT_TIMEOUT_MS;
  }

  function enhanceBoardDeleteDialogs(root = document) {
    if (typeof HTMLDialogElement !== 'function') return;
    root.querySelectorAll('.request-board__delete').forEach((details) => {
      if (details.dataset.enhanced) return;
      const trigger = details.querySelector('summary');
      const panel = details.querySelector('.request-board__delete-panel');
      if (!trigger || !panel || !trigger.id) return;
      details.dataset.enhanced = 'true';
      const dialog = document.createElement('dialog');
      dialog.className = panel.className;
      const labelEl = panel.querySelector('[id^=delete-label-]');
      const descEl = panel.querySelector('[id^=delete-description-]');
      if (labelEl?.id) dialog.setAttribute('aria-labelledby', labelEl.id);
      if (descEl?.id) dialog.setAttribute('aria-describedby', descEl.id);
      dialog.append(...panel.childNodes);
      panel.remove();
      details.after(dialog);
      const fallbackCancel = dialog.querySelector('.request-board__delete-cancel');
      if (fallbackCancel) {
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = fallbackCancel.className;
        cancel.textContent = fallbackCancel.textContent;
        cancel.addEventListener('click', () => dialog.close());
        fallbackCancel.replaceWith(cancel);
      }
      trigger.setAttribute('aria-haspopup', 'dialog');
      dialog.id = `delete-dialog-${trigger.id}`;
      trigger.setAttribute('aria-controls', dialog.id);
      trigger.setAttribute('aria-expanded', 'false');
      trigger.addEventListener('click', (event) => {
        event.preventDefault();
        if (dialog.open) return;
        trigger.setAttribute('aria-expanded', 'true');
        dialog.showModal();
        dialog.querySelector('button')?.focus();
      });
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
      });
      dialog.addEventListener('keydown', (event) => {
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled)')];
        if (!controls.length) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      });
      dialog.addEventListener('close', () => {
        trigger.setAttribute('aria-expanded', 'false');
        if (!details.dataset.removing && trigger.isConnected) trigger.focus({ preventScroll: true });
      });
    });
  }

  enhanceBoardDeleteDialogs();

  document.addEventListener('click', (event) => {
    const cancel = event.target.closest('.request-board__delete-cancel');
    const details = cancel?.closest('details');
    if (details) {
      event.preventDefault();
      details.open = false;
      details.querySelector('summary')?.focus();
    }
  });

  function setupFilterDisclosure() {
    const form = document.querySelector('[data-filter-form]');
    if (!form) return;
    const panel = document.querySelector('[data-filter-panel]');
    const trigger = panel?.querySelector('[data-filter-open]');
    if (panel && trigger) {
      const syncExpanded = () => trigger.setAttribute('aria-expanded', String(panel.open));
      syncExpanded();
      panel.addEventListener('toggle', syncExpanded);
      document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !panel.open) return;
        const active = document.activeElement;
        if (!active || !panel.contains(active)) return;
        panel.open = false;
        trigger.focus({ preventScroll: true });
      });
    }
    form.addEventListener('board:filter-applied', (event) => {
      const params = event.detail || new URL(window.location.href).searchParams;
      for (const name of ['title', 'food_item', 'status', 'location', 'sort', 'q', 'mine']) {
        const field = form.elements[name];
        if (!field) continue;
        if (name === 'mine') {
          field.value = ['on', 'true', '1'].includes((params.get('mine') || '').toLowerCase()) ? 'on' : '';
        } else {
          field.value = params.get(name) || '';
        }
      }
      if (panel?.open) panel.open = false;
    });
  }

  setupFilterDisclosure();

  const boardRoot = document.querySelector('[data-board-root], [data-board-offers], [data-create-owner], .request-board');
  const results = document.querySelector('[data-board-results]');
  const createForm = document.querySelector('[data-board-create]');
  const offers = document.querySelector('[data-board-offers]');
  const filter = document.querySelector('[data-filter-form]');
  const boardStatus = document.querySelector('[data-board-status]');

  if (!window.fetch || !window.AbortController || !window.URLSearchParams || !window.FormData || (!results && !createForm && !offers)) {
    return;
  }

  function replaceSentinel(template, sentinel, value) {
    if (!template) return '';
    return template.replace(sentinel, encodeURIComponent(String(value)));
  }

  function getConfig() {
    const root = document.querySelector('[data-board-root]')
      || document.querySelector('[data-board-offers]')
      || document.querySelector('.request-board')
      || boardRoot;
    const ds = root?.dataset || {};
    const dialog = document.querySelector('[data-create-dialog]');
    return {
      currentUserId: ds.currentUserId || ds.createOwner || dialog?.dataset.createOwner || '',
      currentUserName: ds.currentUserName || dialog?.dataset.createOwnerName || document.querySelector('.navbar__username')?.textContent.trim() || '',
      loginUrl: ds.loginUrl || '/login/',
      boardUrl: ds.boardUrl || '/board/',
      createUrl: ds.createUrl || '/board/new/',
      apiRequestsUrl: ds.apiRequestsUrl || '',
      apiDetailUrl: (id) => replaceSentinel(ds.apiDetailTemplate, '111111111', id),
      apiOffersUrl: (id) => replaceSentinel(ds.apiOffersTemplate, '111111111', id),
      apiOfferDetailUrl: (id, offerId) => replaceSentinel(
        replaceSentinel(ds.apiOfferDetailTemplate, '111111111', id),
        '222222222',
        offerId,
      ),
      apiSelectionUrl: (id) => replaceSentinel(ds.apiSelectionTemplate, '111111111', id),
      apiStatusUrl: (id) => replaceSentinel(ds.apiStatusTemplate, '111111111', id),
      apiCreationStatusUrl: (key) => replaceSentinel(
        ds.apiCreationStatusTemplate,
        '00000000-0000-0000-0000-000000000000',
        key,
      ),
      offersPageUrl: (id) => replaceSentinel(ds.offersPageTemplate, '111111111', id),
      offerFallbackUrl: (id) => replaceSentinel(ds.offerFallbackTemplate, '111111111', id),
      fulfillFallbackUrl: (id) => replaceSentinel(ds.fulfillFallbackTemplate, '111111111', id),
      deleteFallbackUrl: (id) => replaceSentinel(ds.deleteFallbackTemplate, '111111111', id),
      staticIconBase: ds.staticIconBase || '/static/request-board/',
      offerUserIcon: ds.offerUserIcon || `${ds.staticIconBase || '/static/request-board/'}offer-user.svg`,
    };
  }

  let controller = null;
  let sequence = 0;
  let offerRefreshController = null;
  let offerRefreshSequence = 0;
  let filterDebounceTimer = null;
  let currentUrl = new URL(window.location.href);
  const pendingMutations = new Set();

  function announce(message) {
    if (!boardStatus) return;
    boardStatus.textContent = '';
    if (message) {
      window.setTimeout(() => { boardStatus.textContent = message; }, 20);
    }
  }

  function getCookie(name) {
    if (!document.cookie) return '';
    const prefix = `${name}=`;
    for (const part of document.cookie.split(';')) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) {
        return decodeURIComponent(trimmed.slice(prefix.length));
      }
    }
    return '';
  }

  function getCsrfToken(form = null) {
    return getCookie('csrftoken')
      || form?.querySelector('[name=csrfmiddlewaretoken]')?.value
      || document.querySelector('[name=csrfmiddlewaretoken]')?.value
      || '';
  }

  function buildLoginUrl(nextPath = window.location.pathname + window.location.search) {
    const cfg = getConfig();
    try {
      const url = new URL(cfg.loginUrl, window.location.href);
      if (nextPath && !url.searchParams.has('next')) {
        url.searchParams.set('next', nextPath);
      }
      return `${url.pathname}${url.search}`;
    } catch {
      return cfg.loginUrl;
    }
  }

  async function timedFetch(url, options = {}) {
    const abort = new AbortController();
    const outerSignal = options.signal;
    const onOuterAbort = () => abort.abort(outerSignal?.reason);
    if (outerSignal) {
      if (outerSignal.aborted) abort.abort(outerSignal.reason);
      else outerSignal.addEventListener('abort', onOuterAbort, { once: true });
    }
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      abort.abort(new DOMException('Request timeout', 'TimeoutError'));
    }, getTimeoutMs());
    try {
      const fetchOptions = { ...options, signal: abort.signal };
      const response = await fetch(url, fetchOptions);
      const text = await response.text();
      return {
        ok: response.ok,
        status: response.status,
        redirected: response.redirected,
        url: response.url,
        headers: response.headers,
        text: async () => text,
        json: async () => {
          if (response.status === 204 || !text.trim()) return null;
          return JSON.parse(text);
        },
      };
    } catch (error) {
      if (timedOut) {
        const timeoutErr = new Error('Request timeout');
        timeoutErr.name = 'TimeoutError';
        throw timeoutErr;
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
      if (outerSignal) outerSignal.removeEventListener('abort', onOuterAbort);
    }
  }

  function safeParseJson(text, status) {
    if (status === 204 || !text || !text.trim()) {
      return { isJson: true, data: null };
    }
    try {
      return { isJson: true, data: JSON.parse(text) };
    } catch {
      return { isJson: false, data: null };
    }
  }

  function normalizeApiError(status, data, isJson, rawText, redirected = false) {
    const fields = {};
    const nonFieldErrors = [];
    let detail = '';
    let code = null;

    if (isJson && data && typeof data === 'object' && !Array.isArray(data)) {
      if (typeof data.detail === 'string') detail = data.detail;
      if (typeof data.code === 'string') code = data.code;
      for (const [key, value] of Object.entries(data)) {
        if (key === 'detail' || key === 'code') continue;
        const list = Array.isArray(value) ? value.map(String) : [String(value)];
        if (key === 'non_field_errors' || key === '__all__') {
          nonFieldErrors.push(...list);
        } else {
          fields[key] = list;
        }
      }
      if (!detail && nonFieldErrors.length) detail = nonFieldErrors[0];
    }

    const isCsrf = status === 403 && /csrf/i.test(detail || rawText || '');
    const isAuth = status === 401
      || (status === 403 && !isCsrf && /authentication|credentials|autentikasi|masuk|login/i.test(detail || ''));
    const hasFields = Object.keys(fields).length > 0 || nonFieldErrors.length > 0;
    const isUncertain = redirected || status >= 500 || (!isJson && status >= 400 && status !== 403);

    if (isCsrf) {
      detail = 'Permintaan ditolak. Periksa sesi dan token CSRF.';
    } else if (/^Authentication credentials were not provided\.?$/i.test(detail)) {
      detail = 'Silakan masuk untuk melanjutkan.';
    } else if (!detail) {
      if (status === 401 || isAuth) detail = 'Silakan masuk untuk melanjutkan.';
      else if (status === 403) detail = 'Anda tidak memiliki izin untuk melakukan tindakan ini.';
      else if (status === 404) detail = 'Permintaan tidak ditemukan.';
      else if (status === 409) detail = 'Permintaan tidak dapat diproses karena statusnya sudah berubah.';
      else if (status === 400 && hasFields) detail = 'Periksa kembali isian formulir.';
      else if (!isJson) detail = 'Respons server tidak valid. Silakan coba lagi.';
      else detail = 'Terjadi kesalahan saat memproses permintaan.';
    }

    return {
      status,
      detail,
      code,
      fields,
      nonFieldErrors,
      hasFields,
      isCsrf,
      isAuth,
      isUncertain,
      isConflict: status === 409,
      isJson,
    };
  }

  async function apiRequest(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { Accept: 'application/json', ...(options.headers || {}) };
    let body = options.body;
    if (options.jsonBody !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.jsonBody);
    }
    if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)) {
      const token = options.csrfToken ?? getCsrfToken(options.form || null);
      if (token) headers['X-CSRFToken'] = token;
    }
    const response = await timedFetch(url, {
      method,
      headers,
      body,
      credentials: 'same-origin',
      signal: options.signal,
      cache: options.cache,
    });
    const rawText = await response.text();
    const parsed = safeParseJson(rawText, response.status);
    const errorInfo = (!response.ok || response.redirected)
      ? normalizeApiError(response.status, parsed.data, parsed.isJson, rawText, response.redirected)
      : null;
    return {
      ok: response.ok && !response.redirected,
      status: response.status,
      redirected: response.redirected,
      headers: response.headers,
      data: parsed.data,
      isJson: parsed.isJson,
      rawText,
      errorInfo,
    };
  }

  function sanitizeSameOriginUrl(rawUrl) {
    if (!rawUrl) return null;
    try {
      const url = new URL(rawUrl, window.location.href);
      if (url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol)) {
        return url.href;
      }
    } catch {
      return null;
    }
    return null;
  }

  function notice(message, isError = false, loginUrl = null) {
    const dialog = isError && document.querySelector('dialog:modal');
    const safeLoginUrl = sanitizeSameOriginUrl(loginUrl);
    if (!dialog && window.showRequestBoardToast) {
      const toast = window.showRequestBoardToast(message, isError, safeLoginUrl);
      if (isError && createForm) toast?.focus();
      return toast;
    }
    let region = document.querySelector('[data-inline-ajax-notice]');
    if (!region) {
      region = document.createElement('div');
      region.dataset.ajaxNotice = '';
      region.dataset.inlineAjaxNotice = '';
      region.className = 'request-board__alert';
      region.tabIndex = -1;
    }
    const container = dialog || document.querySelector('.request-board') || document.body;
    if (region.parentElement !== container) container.prepend(region);
    region.className = `request-board__alert request-board__alert--${isError ? 'error' : 'success'}`;
    region.setAttribute('role', isError ? 'alert' : 'status');
    region.setAttribute('aria-live', isError ? 'assertive' : 'polite');
    region.replaceChildren(document.createTextNode(message));
    if (safeLoginUrl) {
      const link = document.createElement('a');
      link.href = safeLoginUrl;
      if (dialog?.hasAttribute('data-create-dialog')) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Masuk di tab baru';
      } else {
        link.textContent = 'Masuk';
      }
      region.append(' ', link);
    }
    if (isError) {
      region.scrollIntoView?.({ block: 'nearest' });
      region.focus();
    }
    return region;
  }

  try {
    const flash = window.sessionStorage?.getItem('request_board_flash_notice');
    if (flash && results) {
      window.sessionStorage.removeItem('request_board_flash_notice');
      notice(flash, false);
    }
  } catch {
    // Ignore storage access errors in private browsing contexts.
  }

  function formatJakartaDateTime(isoString, withWib = false) {
    if (!isoString) return '';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    const wib = new Date(date.getTime() + 7 * 60 * 60 * 1000);
    const dd = String(wib.getUTCDate()).padStart(2, '0');
    const mmm = MONTHS[wib.getUTCMonth()];
    const yyyy = wib.getUTCFullYear();
    const hh = String(wib.getUTCHours()).padStart(2, '0');
    const mm = String(wib.getUTCMinutes()).padStart(2, '0');
    return `${dd} ${mmm} ${yyyy}, ${hh}:${mm}${withWib ? ' WIB' : ''}`;
  }

  function formatOfferPageDateTime(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    const wib = new Date(date.getTime() + 7 * 60 * 60 * 1000);
    const dd = String(wib.getUTCDate()).padStart(2, '0');
    const mmm = MONTHS[wib.getUTCMonth()];
    const yyyy = wib.getUTCFullYear();
    const hh = String(wib.getUTCHours()).padStart(2, '0');
    const mm = String(wib.getUTCMinutes()).padStart(2, '0');
    return `${dd} ${mmm} ${yyyy} · ${hh}:${mm}`;
  }

  function formatQuantityDisplay(quantity) {
    if (quantity === null || quantity === undefined) return '';
    const str = String(quantity);
    if (!str.includes('.')) return str;
    return str.replace(/\.?0+$/, '');
  }

  function clearErrors(form) {
    document.querySelector('.request-board__error-summary')?.remove();
    form.querySelectorAll('[data-ajax-field-error], .request-board__field-errors').forEach((element) => element.remove());
    form.querySelectorAll('[aria-invalid]').forEach((element) => element.removeAttribute('aria-invalid'));
    form.querySelectorAll('[aria-describedby]').forEach((element) => {
      const ids = element.getAttribute('aria-describedby')
        .split(/\s+/)
        .filter((id) => id && !id.startsWith('ajax-error-') && !id.endsWith('_error'));
      if (ids.length) element.setAttribute('aria-describedby', ids.join(' '));
      else element.removeAttribute('aria-describedby');
    });
  }

  function showErrors(form, fields) {
    clearErrors(form);
    let firstInvalid = null;
    for (const [name, messages] of Object.entries(fields || {})) {
      const list = Array.isArray(messages) ? messages : [messages];
      if (!list.length) continue;
      if (name === '__all__' || name === 'non_field_errors') {
        notice(String(list[0]), true);
        continue;
      }
      const input = form.elements.namedItem(name);
      if (!input || (typeof RadioNodeList !== 'undefined' && input instanceof RadioNodeList) || !input.closest) {
        notice(String(list[0]), true);
        continue;
      }
      input.setAttribute('aria-invalid', 'true');
      const error = document.createElement('div');
      error.id = `ajax-error-${name}-${Math.random().toString(36).slice(2, 7)}`;
      error.dataset.ajaxFieldError = '';
      error.className = 'request-board__field-errors';
      const ul = document.createElement('ul');
      ul.className = 'errorlist';
      list.forEach((msg) => {
        const li = document.createElement('li');
        li.textContent = String(msg);
        ul.append(li);
      });
      error.append(ul);
      input.setAttribute(
        'aria-describedby',
        [input.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '),
      );
      (input.closest('.request-board__form-group, .request-board__filter-field') || input.parentElement).append(error);
      if (!firstInvalid) firstInvalid = input;
    }
    if (firstInvalid) firstInvalid.focus();
  }

  function buildStatusBadge(effectiveStatus, selectedOfferId, cfg) {
    const badge = document.createElement('span');
    badge.className = `request-board__badge request-board__badge--${effectiveStatus}`;

    const picture = document.createElement('picture');
    picture.className = 'request-board__status-icon';
    if (effectiveStatus !== 'fulfilled' && effectiveStatus !== 'expired') {
      const source = document.createElement('source');
      source.media = '(max-width: 768px)';
      source.srcset = `${cfg.staticIconBase}${effectiveStatus}-mobile.svg`;
      picture.append(source);
    }
    const img = document.createElement('img');
    const iconName = effectiveStatus === 'expired' ? 'pending' : effectiveStatus;
    img.src = `${cfg.staticIconBase}${iconName}.svg`;
    img.alt = '';
    picture.append(img);

    const text = (selectedOfferId && effectiveStatus !== 'fulfilled' && effectiveStatus !== 'expired')
      ? 'Menunggu pemenuhan'
      : (STATUS_LABELS[effectiveStatus] || effectiveStatus);

    badge.append(picture, document.createTextNode(` ${text}`));
    return badge;
  }

  function buildMetaItem(labelText, valueNode, extraClass = '') {
    const item = document.createElement('div');
    item.className = extraClass
      ? `request-board__meta-item ${extraClass}`
      : 'request-board__meta-item';
    const label = document.createElement('span');
    label.className = 'request-board__meta-label';
    label.textContent = labelText;
    item.append(label, valueNode);
    return item;
  }

  function buildMetaValue(text) {
    const span = document.createElement('span');
    span.className = 'request-board__meta-value';
    span.textContent = text;
    return span;
  }

  function buildActionButton(label, classes, iconBase, iconDesktop, iconMobile = null) {
    const btn = document.createElement('button');
    btn.type = 'submit';
    btn.className = classes;
    if (iconDesktop) {
      if (iconMobile) {
        const pic = document.createElement('picture');
        const src = document.createElement('source');
        src.media = '(max-width: 768px)';
        src.srcset = `${iconBase}${iconMobile}`;
        const img = document.createElement('img');
        img.src = `${iconBase}${iconDesktop}`;
        img.alt = '';
        img.width = 24;
        img.height = 24;
        pic.append(src, img);
        btn.append(pic);
      } else {
        const img = document.createElement('img');
        img.src = `${iconBase}${iconDesktop}`;
        img.alt = '';
        img.width = 24;
        img.height = 24;
        btn.append(img);
      }
    }
    btn.append(document.createTextNode(label));
    return btn;
  }

  function buildCreateIconPicture(cfg) {
    const pic = document.createElement('picture');
    const src = document.createElement('source');
    src.media = '(max-width: 768px)';
    src.srcset = `${cfg.staticIconBase}create-mobile.svg`;
    const img = document.createElement('img');
    img.src = `${cfg.staticIconBase}create.svg`;
    img.alt = '';
    img.width = 24;
    img.height = 24;
    pic.append(src, img);
    return pic;
  }

  async function hydrateOwnedCardOffers(card, item, isOwner, seqId, cfg) {
    if (!isOwner || !item.offer_count) return;
    const footerSlot = card.querySelector('[data-owner-offers-slot]');
    if (!footerSlot) return;

    if (item.selected_offer_id) {
      const detailUrl = cfg.apiOfferDetailUrl(item.id, item.selected_offer_id);
      if (!detailUrl) return;
      try {
        const res = await apiRequest(detailUrl);
        if (seqId !== sequence || !card.isConnected) return;
        if (res.ok && res.data?.offered_by?.display_name) {
          const note = document.createElement('p');
          note.className = 'request-board__card-note';
          note.textContent = `Penawar terpilih: ${res.data.offered_by.display_name}`;
          footerSlot.prepend(note);
        }
      } catch {
        // Non-fatal: link to "Lihat semua penawaran" remains visible.
      }
      return;
    }

    const offersUrl = cfg.apiOffersUrl(item.id);
    if (!offersUrl) return;
    try {
      const res = await apiRequest(`${offersUrl}?page=1`);
      if (seqId !== sequence || !card.isConnected) return;
      if (!res.ok || !Array.isArray(res.data?.results) || !res.data.results.length) return;
      const donorsBox = document.createElement('div');
      donorsBox.className = 'request-board__donors';
      const title = document.createElement('h3');
      title.className = 'request-board__donors-title';
      title.textContent = 'Penawaran terbaru';
      const ul = document.createElement('ul');
      ul.className = 'request-board__donors-list tw:flex tw:flex-col tw:gap-2';
      res.data.results.slice(0, 20).forEach((offer) => {
        const li = document.createElement('li');
        li.className = 'request-board__donor tw:flex tw:flex-wrap tw:items-baseline tw:gap-1';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'request-board__donor-name';
        nameSpan.textContent = offer.offered_by?.display_name || '';
        const time = document.createElement('time');
        time.className = 'request-board__donor-time';
        time.dateTime = offer.created_at || '';
        time.textContent = formatJakartaDateTime(offer.created_at, false);
        li.append(nameSpan, time);
        ul.append(li);
      });
      donorsBox.append(title, ul);
      if (Number(res.data.count || item.offer_count) > 20) {
        const more = document.createElement('p');
        more.className = 'request-board__donors-more';
        more.textContent = 'Menampilkan 20 penawaran terbaru; penawaran lainnya tidak ditampilkan.';
        donorsBox.append(more);
      }
      footerSlot.prepend(donorsBox);
    } catch {
      // Non-fatal: link to "Lihat semua penawaran" remains visible.
    }
  }

  function renderRequestCard(item, cfg, seqId) {
    const effectiveStatus = item.effective_status || item.status || 'pending';
    const currentUserId = cfg.currentUserId ? String(cfg.currentUserId) : '';
    const isAuthenticated = Boolean(currentUserId);
    const isOwner = isAuthenticated && String(item.owner?.id) === currentUserId;
    const isClosed = effectiveStatus === 'fulfilled' || effectiveStatus === 'expired';
    const csrfToken = getCsrfToken();

    const article = document.createElement('article');
    article.className = `request-board__card request-board__card--${effectiveStatus} tw:flex tw:min-w-0 tw:flex-col tw:gap-4`;
    article.dataset.requestId = String(item.id);

    const header = document.createElement('div');
    header.className = 'request-board__card-header';
    header.append(buildStatusBadge(effectiveStatus, item.selected_offer_id, cfg));

    const title = document.createElement('h2');
    title.id = `request-title-${item.id}`;
    title.className = 'request-board__card-title';
    title.textContent = item.title || '';

    const meta = document.createElement('div');
    meta.className = 'request-board__card-meta';
    meta.append(buildMetaItem('Bahan', buildMetaValue(item.food_item || ''), 'request-board__meta-item--primary'));

    const unitLabel = UNIT_LABELS[item.unit] || item.unit || '';
    meta.append(buildMetaItem(
      'Jumlah',
      buildMetaValue(`${item.quantity ?? ''} ${unitLabel}`.trim()),
      'request-board__meta-item--primary',
    ));

    const ownerVal = document.createElement('span');
    ownerVal.className = 'request-board__meta-value';
    ownerVal.textContent = item.owner?.display_name || '';
    if (isOwner) {
      const you = document.createElement('span');
      you.className = 'request-board__owner-indicator';
      you.textContent = '(Anda)';
      ownerVal.append(document.createTextNode(' '), you);
    }
    meta.append(buildMetaItem('Pemohon', ownerVal, 'request-board__meta-item--owner'));

    const offerCountVal = document.createElement('span');
    offerCountVal.className = 'request-board__meta-value';
    const offerCount = Number(item.offer_count || 0);
    offerCountVal.dataset.offerCount = String(offerCount);
    offerCountVal.textContent = `${offerCount} penawaran`;
    meta.append(buildMetaItem('Penawaran', offerCountVal));

    if (item.location) {
      meta.append(buildMetaItem('Lokasi', buildMetaValue(item.location)));
    }

    article.append(header, title, meta);

    if (item.description) {
      const descWrap = document.createElement('div');
      descWrap.className = 'request-board__card-desc';
      const descP = document.createElement('p');
      descP.textContent = item.description;
      descWrap.append(descP);
      article.append(descWrap);
    }

    if (isOwner) {
      const ownerSlot = document.createElement('div');
      ownerSlot.dataset.ownerOffersSlot = '';
      if (offerCount > 0) {
        const offersLinkWrap = document.createElement('div');
        offersLinkWrap.className = 'request-board__offers-link-action';
        const offersLink = document.createElement('a');
        offersLink.className = 'btn btn--tertiary btn--bright request-board__offers-link';
        offersLink.href = cfg.offersPageUrl(item.id) || `#offers-${item.id}`;
        offersLink.textContent = 'Lihat semua penawaran';
        offersLinkWrap.append(offersLink);
        ownerSlot.append(offersLinkWrap);
      }
      if (ownerSlot.childNodes.length || item.selected_offer_id || offerCount > 0) {
        article.append(ownerSlot);
        hydrateOwnedCardOffers(article, item, true, seqId, cfg);
      }
    } else if (item.selected_offer_id && !isClosed) {
      const closedNote = document.createElement('p');
      closedNote.className = 'request-board__card-note request-board__closure-note';
      closedNote.textContent = 'Penawaran baru ditutup karena penawar sudah dipilih.';
      article.append(closedNote);
    }

    if (item.expires_at) {
      const expiresEl = document.createElement('time');
      expiresEl.className = 'request-board__card-time';
      expiresEl.dateTime = item.expires_at;
      expiresEl.textContent = `Batas waktu: ${formatJakartaDateTime(item.expires_at, true)}`;
      article.append(expiresEl);
    }

    if (item.created_at) {
      const createdEl = document.createElement('time');
      createdEl.className = 'request-board__card-time';
      createdEl.dateTime = item.created_at;
      createdEl.textContent = `Dibuat · ${formatJakartaDateTime(item.created_at, false)}`;
      article.append(createdEl);
    }

    if (isAuthenticated && !isOwner && !item.selected_offer_id && !isClosed) {
      const actions = document.createElement('div');
      actions.className = 'request-board__card-actions';
      if (item.has_offered) {
        const note = document.createElement('p');
        note.className = 'request-board__card-note';
        note.textContent = 'Sudah menawarkan';
        actions.append(note);
      } else {
        const form = document.createElement('form');
        form.method = 'post';
        form.action = cfg.offerFallbackUrl(item.id);
        form.dataset.cardAction = 'offer';
        if (csrfToken) {
          const csrfInput = document.createElement('input');
          csrfInput.type = 'hidden';
          csrfInput.name = 'csrfmiddlewaretoken';
          csrfInput.value = csrfToken;
          form.append(csrfInput);
        }
        const btn = buildActionButton('Saya Punya!', 'btn btn--secondary btn--bright request-board__action', cfg.staticIconBase, null);
        form.append(btn);
        actions.append(form);
      }
      article.append(actions);
    }

    if (isOwner) {
      const ownerActions = document.createElement('div');
      ownerActions.className = 'request-board__owner-actions';

      if (effectiveStatus === 'offered' && item.selected_offer_id) {
        const fulfillForm = document.createElement('form');
        fulfillForm.method = 'post';
        fulfillForm.action = cfg.fulfillFallbackUrl(item.id);
        fulfillForm.dataset.cardAction = 'fulfill';
        if (csrfToken) {
          const csrfInput = document.createElement('input');
          csrfInput.type = 'hidden';
          csrfInput.name = 'csrfmiddlewaretoken';
          csrfInput.value = csrfToken;
          fulfillForm.append(csrfInput);
        }
        fulfillForm.append(buildActionButton('Tandai Terpenuhi', 'btn btn--secondary btn--bright request-board__action', cfg.staticIconBase, null));
        ownerActions.append(fulfillForm);
      }

      const details = document.createElement('details');
      details.className = 'request-board__delete';

      const summary = document.createElement('summary');
      summary.className = 'request-board__delete-summary';
      summary.id = `delete-summary-${item.id}`;
      summary.setAttribute('aria-describedby', `delete-warning-${item.id}`);
      const delPic = document.createElement('picture');
      const delSrc = document.createElement('source');
      delSrc.media = '(max-width: 768px)';
      delSrc.srcset = `${cfg.staticIconBase}trash-mobile.svg`;
      const delImg = document.createElement('img');
      delImg.src = `${cfg.staticIconBase}trash.svg`;
      delImg.alt = '';
      delImg.width = 24;
      delImg.height = 24;
      delPic.append(delSrc, delImg);
      summary.append(delPic, document.createTextNode('Hapus'));

      const panel = document.createElement('div');
      panel.className = 'request-board__delete-panel';

      const iconWrap = document.createElement('span');
      iconWrap.className = 'request-board__delete-icon';
      iconWrap.setAttribute('aria-hidden', 'true');
      const largeImg = document.createElement('img');
      largeImg.src = `${cfg.staticIconBase}trash-large.svg`;
      largeImg.alt = '';
      largeImg.width = 24;
      largeImg.height = 24;
      iconWrap.append(largeImg);

      const warning = document.createElement('p');
      warning.id = `delete-warning-${item.id}`;
      warning.className = 'request-board__delete-warning';
      const labelSpan = document.createElement('span');
      labelSpan.id = `delete-label-${item.id}`;
      labelSpan.append(document.createTextNode('Hapus '));
      const targetStrong = document.createElement('strong');
      targetStrong.id = `delete-target-${item.id}`;
      targetStrong.textContent = item.title || '';
      labelSpan.append(targetStrong, document.createTextNode('?'));
      const descSpan = document.createElement('span');
      descSpan.id = `delete-description-${item.id}`;
      descSpan.textContent = 'Permintaan akan dihapus permanen. Tindakan ini tidak dapat dibatalkan.';
      warning.append(labelSpan, document.createTextNode(' '), descSpan);

      const delActions = document.createElement('div');
      delActions.className = 'request-board__delete-actions';
      const cancelLink = document.createElement('a');
      cancelLink.href = cfg.boardUrl;
      cancelLink.className = 'request-board__delete-cancel';
      cancelLink.textContent = 'Batal';

      const delForm = document.createElement('form');
      delForm.method = 'post';
      delForm.action = cfg.deleteFallbackUrl(item.id);
      delForm.dataset.cardAction = 'delete';
      if (csrfToken) {
        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrfmiddlewaretoken';
        csrfInput.value = csrfToken;
        delForm.append(csrfInput);
      }
      const delBtn = buildActionButton('Ya, hapus', 'btn btn--danger request-board__action request-board__delete-confirm', cfg.staticIconBase, 'trash.svg');
      delBtn.id = `delete-confirm-${item.id}`;
      delBtn.setAttribute('aria-describedby', `delete-warning-${item.id}`);
      delForm.append(delBtn);

      delActions.append(cancelLink, delForm);
      panel.append(iconWrap, warning, delActions);
      details.append(summary, panel);
      ownerActions.append(details);
      article.append(ownerActions);
    }

    return article;
  }

  function hasActiveFilters(searchParams) {
    for (const key of ['q', 'title', 'food_item', 'location', 'status', 'mine']) {
      if (searchParams.get(key)) return true;
    }
    const sortVal = searchParams.get('sort');
    return Boolean(sortVal && sortVal !== 'newest');
  }

  function buildBoardPageHref(baseUrl, searchParams, pageNum) {
    const next = new URL(baseUrl, window.location.href);
    next.search = '';
    for (const [k, v] of searchParams.entries()) {
      if (k !== 'page' && v) next.searchParams.set(k, v);
    }
    next.searchParams.set('page', String(pageNum));
    return `${next.pathname}${next.search}`;
  }

  function renderBoardResults(container, apiData, searchParams, cfg, seqId) {
    const items = Array.isArray(apiData?.results) ? apiData.results : [];
    const total = Number(apiData?.count ?? items.length);
    const page = Number(apiData?.page ?? 1);
    const totalPages = Number(apiData?.total_pages ?? 1);
    const filtersActive = hasActiveFilters(searchParams);

    const fragment = document.createDocumentFragment();
    const summary = document.createElement('span');
    summary.hidden = true;
    summary.dataset.boardResultSummary = '';
    summary.dataset.total = String(total);
    summary.dataset.page = String(page);
    summary.dataset.pages = String(totalPages);
    fragment.append(summary);

    if (items.length) {
      const grid = document.createElement('div');
      grid.className = 'request-board__cards tw:grid tw:grid-cols-1 tw:items-start tw:gap-6 tw:min-[769px]:grid-cols-2 tw:min-[769px]:gap-x-4 tw:min-[769px]:gap-y-11';
      items.forEach((item) => grid.append(renderRequestCard(item, cfg, seqId)));
      fragment.append(grid);

      if (totalPages > 1) {
        const nav = document.createElement('nav');
        nav.className = 'request-board__pagination';
        nav.setAttribute('aria-label', 'Navigasi halaman');
        if (page > 1) {
          const prev = document.createElement('a');
          prev.href = buildBoardPageHref(cfg.boardUrl, searchParams, page - 1);
          prev.className = 'btn btn--tertiary btn--dark-green request-board__pagination-link';
          prev.textContent = 'Sebelumnya';
          nav.append(prev);
        }
        const info = document.createElement('span');
        info.className = 'request-board__pagination-info';
        info.tabIndex = -1;
        info.dataset.boardResultsFocus = '';
        const currentSpan = document.createElement('span');
        currentSpan.className = 'request-board__pagination-current';
        currentSpan.setAttribute('aria-current', 'page');
        currentSpan.textContent = String(page);
        info.append(
          document.createTextNode('Halaman '),
          currentSpan,
          document.createTextNode(` dari ${totalPages}`),
        );
        nav.append(info);
        if (page < totalPages) {
          const next = document.createElement('a');
          next.href = buildBoardPageHref(cfg.boardUrl, searchParams, page + 1);
          next.className = 'btn btn--tertiary btn--dark-green request-board__pagination-link';
          const icon = document.createElement('img');
          icon.src = `${cfg.staticIconBase}next.svg`;
          icon.alt = '';
          icon.width = 18;
          icon.height = 18;
          next.append(icon, document.createTextNode(' Berikutnya'));
          nav.append(next);
        }
        fragment.append(nav);
      }
    } else if (filtersActive) {
      const empty = document.createElement('div');
      empty.className = 'request-board__empty-state';
      const h2 = document.createElement('h2');
      h2.className = 'request-board__empty-title';
      h2.textContent = 'Tidak Ada Hasil';
      const p = document.createElement('p');
      p.className = 'request-board__empty-text';
      p.textContent = 'Tidak ada permintaan yang cocok. Coba ubah atau reset filter.';
      const resetLink = document.createElement('a');
      resetLink.href = cfg.boardUrl;
      resetLink.dataset.boardReset = '';
      resetLink.className = 'btn btn--tertiary btn--dark-green tw:min-h-11 tw:rounded-xl! tw:text-base! tw:min-[769px]:min-h-[52px] tw:min-[769px]:rounded-button! tw:min-[769px]:text-2xl!';
      resetLink.textContent = 'Reset Filter';
      empty.append(h2, p, resetLink);
      fragment.append(empty);
    } else {
      const empty = document.createElement('div');
      empty.className = 'request-board__empty-state';
      const h2 = document.createElement('h2');
      h2.className = 'request-board__empty-title';
      h2.textContent = 'Belum Ada Permintaan';
      const p = document.createElement('p');
      p.className = 'request-board__empty-text';
      p.textContent = 'Belum ada permintaan bantuan makanan saat ini. Anda dapat membuat permintaan baru untuk kebutuhan bahan makanan.';
      const actionWrap = document.createElement('div');
      actionWrap.className = 'request-board__empty-action';
      const cta = document.createElement('a');
      cta.className = 'btn btn--primary';
      if (cfg.currentUserId) {
        cta.href = cfg.createUrl;
        cta.append(buildCreateIconPicture(cfg), document.createTextNode(' Buat Permintaan'));
      } else {
        cta.href = buildLoginUrl(cfg.createUrl);
        cta.append(buildCreateIconPicture(cfg), document.createTextNode(' Masuk untuk Buat Permintaan'));
      }
      actionWrap.append(cta);
      empty.append(h2, p, actionWrap);
      fragment.append(empty);
    }

    container.replaceChildren(fragment);
    return { total, page, pages: totalPages };
  }

  function toApiRequestsUrl(boardUrl, cfg) {
    const apiBase = cfg.apiRequestsUrl;
    if (!apiBase) return null;
    const apiUrl = new URL(apiBase, window.location.href);
    const params = new URL(boardUrl, window.location.href).searchParams;
    for (const [key, value] of params.entries()) {
      if (!value) continue;
      if (key === 'mine') {
        apiUrl.searchParams.set('mine', 'true');
      } else {
        apiUrl.searchParams.set(key, value);
      }
    }
    return apiUrl;
  }

  async function boardGet(url, correctPage = false, push = true, notifyOnError = true) {
    if (!results) return false;
    const cfg = getConfig();
    if (!cfg.apiRequestsUrl) return false;

    const confirmedCards = [...document.querySelectorAll('[data-create-card][data-local-id]')]
      .filter((card) => card.dataset.state === 'refreshing' || card.dataset.state === 'saved')
      .map((card) => card.dataset.localId);

    if (controller) controller.abort();
    controller = new AbortController();
    const activeController = controller;
    const id = ++sequence;
    results.setAttribute('aria-busy', 'true');
    announce('Memuat permintaan.');

    try {
      const targetUrl = new URL(url, window.location.href);
      let apiUrl = toApiRequestsUrl(targetUrl, cfg);
      let res = await apiRequest(apiUrl.href, { signal: activeController.signal, cache: 'no-store' });
      let page = Number(targetUrl.searchParams.get('page') || 1);

      while (correctPage && !res.ok && res.status === 404 && Number.isSafeInteger(page) && page > 1) {
        page -= 1;
        if (page === 1) targetUrl.searchParams.delete('page');
        else targetUrl.searchParams.set('page', String(page));
        apiUrl = toApiRequestsUrl(targetUrl, cfg);
        res = await apiRequest(apiUrl.href, { signal: activeController.signal, cache: 'no-store' });
      }

      if (id !== sequence) return false;

      if (!res.ok) {
        if (notifyOnError) {
          if (res.status === 403) {
            const msg = res.errorInfo?.isAuth
              ? 'Silakan masuk untuk menggunakan filter ini.'
              : (res.errorInfo?.detail || 'Permintaan ditolak. Periksa sesi dan token CSRF.');
            notice(msg, true, res.errorInfo?.isAuth ? buildLoginUrl() : null);
          } else if (res.status === 400 && res.errorInfo?.hasFields && filter) {
            showErrors(filter, res.errorInfo.fields);
            notice(res.errorInfo.detail || 'Filter atau halaman tidak valid.', true);
          } else if (res.status === 400 || res.status === 404) {
            notice(res.errorInfo?.detail || 'Filter atau halaman tidak valid.', true);
          } else {
            notice(res.errorInfo?.detail || 'Gagal memuat papan. Periksa jaringan dan coba lagi.', true);
          }
        }
        announce(res.errorInfo?.detail || 'Filter atau halaman tidak valid.');
        return false;
      }

      const rendered = renderBoardResults(results, res.data, targetUrl.searchParams, cfg, id);
      if (filter) clearErrors(filter);
      enhanceBoardDeleteDialogs(results);
      results.dispatchEvent(new CustomEvent('board:results-replaced', { detail: confirmedCards }));
      currentUrl = new URL(targetUrl.href);
      if (push) window.history.pushState(null, '', currentUrl.href);
      if (rendered.total) {
        announce(`Menampilkan ${rendered.total} permintaan, halaman ${rendered.page} dari ${rendered.pages}.`);
      } else {
        announce('Tidak ada permintaan.');
      }
      return true;
    } catch (error) {
      if (error?.name === 'AbortError' && id !== sequence) return false;
      if (id !== sequence) return false;
      announce('Gagal memuat papan. Periksa jaringan dan coba lagi.');
      if (notifyOnError) {
        notice(error?.name === 'TimeoutError'
          ? 'Permintaan ke server habis waktu. Periksa jaringan dan coba lagi.'
          : 'Gagal memuat papan. Periksa jaringan dan coba lagi.', true);
      }
      return false;
    } finally {
      if (id === sequence) results.setAttribute('aria-busy', 'false');
    }
  }

  function renderOffersSection(container, requestItem, offerListData, pageNum, cfg) {
    const effectiveStatus = requestItem.effective_status || requestItem.status || 'pending';
    const isClosed = effectiveStatus === 'fulfilled' || effectiveStatus === 'expired';
    const selectedId = requestItem.selected_offer_id;
    const list = Array.isArray(offerListData?.results) ? offerListData.results : [];
    const totalCount = Number(offerListData?.count ?? requestItem.offer_count ?? list.length);
    const page = Number(offerListData?.page ?? pageNum ?? 1);
    const totalPages = Number(offerListData?.total_pages ?? 1);
    const csrfToken = getCsrfToken();

    const fragment = document.createDocumentFragment();

    const backTop = document.createElement('a');
    backTop.className = 'request-board__offer-back request-board__offer-back--outline';
    backTop.href = cfg.boardUrl;
    backTop.textContent = 'Kembali ke papan';
    fragment.append(backTop);

    const heading = document.createElement('header');
    heading.className = 'request-board__offers-heading';
    const h1 = document.createElement('h1');
    h1.textContent = 'Semua penawaran';
    const sub = document.createElement('p');
    sub.textContent = 'Lihat penawar dan pilih satu untuk permintaan Anda.';
    heading.append(h1, sub);
    fragment.append(heading);

    const summarySection = document.createElement('section');
    summarySection.className = 'request-board__offer-summary';
    summarySection.setAttribute('aria-label', 'Permintaan Anda');
    summarySection.append(buildStatusBadge(effectiveStatus, selectedId, cfg));
    const reqTitle = document.createElement('h2');
    reqTitle.className = 'request-board__offer-request-title';
    reqTitle.textContent = requestItem.title || '';
    const reqMeta = document.createElement('p');
    reqMeta.className = 'request-board__offer-meta';
    const parts = [
      requestItem.food_item || '',
      `${formatQuantityDisplay(requestItem.quantity)} ${requestItem.unit || ''}`.trim(),
    ];
    if (requestItem.location) parts.push(requestItem.location);
    reqMeta.textContent = parts.filter(Boolean).join(' · ');
    const createdTime = document.createElement('time');
    createdTime.dateTime = requestItem.created_at || '';
    createdTime.textContent = `Dibuat ${formatOfferPageDateTime(requestItem.created_at)}`;
    summarySection.append(reqTitle, reqMeta, createdTime);
    fragment.append(summarySection);

    const selectedOfferObj = list.find((o) => Number(o.id) === Number(selectedId)) || null;
    if (selectedId) {
      const banner = document.createElement('div');
      banner.className = 'request-board__offer-banner';
      banner.tabIndex = -1;
      banner.dataset.offerSelection = '';
      const strong = document.createElement('strong');
      strong.dataset.selectedDonorBannerName = '';
      strong.textContent = selectedOfferObj?.offered_by?.display_name
        ? `Penawar terpilih: ${selectedOfferObj.offered_by.display_name}`
        : 'Penawar telah dipilih';
      const p = document.createElement('p');
      if (effectiveStatus === 'fulfilled') p.textContent = 'Permintaan telah terpenuhi.';
      else if (effectiveStatus === 'expired') p.textContent = 'Permintaan telah kedaluwarsa.';
      else p.textContent = 'Penawaran baru ditutup. Anda masih bisa mengganti pilihan.';
      banner.append(strong, p);
      fragment.append(banner);

      if (!selectedOfferObj && requestItem.id && cfg.apiOfferDetailUrl(requestItem.id, selectedId)) {
        apiRequest(cfg.apiOfferDetailUrl(requestItem.id, selectedId)).then((detailRes) => {
          if (detailRes.ok && detailRes.data?.offered_by?.display_name && strong.isConnected) {
            strong.textContent = `Penawar terpilih: ${detailRes.data.offered_by.display_name}`;
          }
        }).catch(() => {});
      }
    }

    const listSection = document.createElement('section');
    listSection.className = 'request-board__offers-list';
    listSection.setAttribute('aria-labelledby', 'offer-count');
    const countH2 = document.createElement('h2');
    countH2.id = 'offer-count';
    countH2.tabIndex = -1;
    countH2.textContent = `${totalCount} penawaran`;
    listSection.append(countH2);

    if (effectiveStatus === 'fulfilled') {
      const ro = document.createElement('p');
      ro.className = 'request-board__offers-readonly';
      ro.textContent = 'Permintaan telah terpenuhi. Daftar ini hanya untuk dilihat.';
      listSection.append(ro);
    } else if (effectiveStatus === 'expired') {
      const ro = document.createElement('p');
      ro.className = 'request-board__offers-readonly';
      ro.textContent = 'Permintaan telah kedaluwarsa. Daftar ini hanya untuk dilihat.';
      listSection.append(ro);
    }

    if (list.length) {
      const ul = document.createElement('ul');
      ul.className = 'request-board__donors-list';
      list.forEach((offer) => {
        const isSelected = Boolean(offer.is_selected || Number(offer.id) === Number(selectedId));
        const li = document.createElement('li');
        li.className = `request-board__donor${isSelected ? ' request-board__donor--selected' : ''}`;

        const identity = document.createElement('div');
        identity.className = 'request-board__offer-identity';
        const img = document.createElement('img');
        img.src = cfg.offerUserIcon;
        img.width = 28;
        img.height = 28;
        img.alt = '';
        const person = document.createElement('div');
        person.className = 'request-board__offer-person';
        const donorName = offer.offered_by?.display_name || '';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'request-board__donor-name';
        nameSpan.textContent = donorName;
        const time = document.createElement('time');
        time.dateTime = offer.created_at || '';
        time.textContent = formatOfferPageDateTime(offer.created_at);
        person.append(nameSpan, time);
        identity.append(img, person);
        li.append(identity);

        if (isSelected) {
          const badge = document.createElement('span');
          badge.className = 'request-board__offer-selected';
          badge.textContent = 'Terpilih';
          li.append(badge);
        } else if (!isClosed) {
          const form = document.createElement('form');
          form.method = 'post';
          form.action = replaceSentinel(container.dataset.offerSelectFallbackTemplate || '', '111111111', offer.id);
          form.dataset.offerId = String(offer.id);
          if (csrfToken) {
            const csrfInput = document.createElement('input');
            csrfInput.type = 'hidden';
            csrfInput.name = 'csrfmiddlewaretoken';
            csrfInput.value = csrfToken;
            form.append(csrfInput);
          }
          const btn = document.createElement('button');
          btn.className = 'request-board__offer-action';
          btn.type = 'submit';
          const labelVerb = selectedId ? 'Ganti pilihan' : 'Pilih penawar';
          btn.setAttribute('aria-label', `${labelVerb} ${donorName}`);
          btn.textContent = labelVerb;
          form.append(btn);
          li.append(form);
        }

        ul.append(li);
      });
      listSection.append(ul);
    } else {
      const empty = document.createElement('div');
      empty.className = 'request-board__offers-empty';
      const h3 = document.createElement('h3');
      h3.textContent = 'Belum ada penawaran';
      const p = document.createElement('p');
      p.textContent = 'Penawaran yang masuk akan muncul di halaman ini.';
      empty.append(h3, p);
      listSection.append(empty);
    }
    fragment.append(listSection);

    if (totalPages > 1) {
      const nav = document.createElement('nav');
      nav.className = 'request-board__offer-pagination';
      nav.setAttribute('aria-label', 'Halaman penawaran');
      const cur = document.createElement('span');
      cur.setAttribute('aria-current', 'page');
      cur.textContent = `Halaman ${page} dari ${totalPages}`;
      nav.append(cur);
      if (page > 1) {
        const prev = document.createElement('a');
        prev.href = `?page=${page - 1}`;
        prev.textContent = 'Sebelumnya';
        nav.append(prev);
      }
      if (page < totalPages) {
        const next = document.createElement('a');
        next.href = `?page=${page + 1}`;
        next.textContent = 'Berikutnya';
        nav.append(next);
      }
      fragment.append(nav);
    }

    if (selectedId && !isClosed) {
      const backBottom = document.createElement('a');
      backBottom.className = 'request-board__offer-back request-board__offer-back--solid';
      backBottom.href = cfg.boardUrl;
      backBottom.textContent = 'Kembali ke papan';
      fragment.append(backBottom);
    }

    container.replaceChildren(fragment);
  }

  async function refreshOffers(targetHref = window.location.href, push = false, focusCount = false, notifyOnError = true) {
    if (!offers) return false;
    const cfg = getConfig();
    const detailUrl = offers.dataset.apiDetailUrl;
    const offersApiUrl = offers.dataset.apiOffersUrl;
    if (!detailUrl || !offersApiUrl) return false;

    if (offerRefreshController) offerRefreshController.abort();
    offerRefreshController = new AbortController();
    const activeController = offerRefreshController;
    const seq = ++offerRefreshSequence;

    const pageUrl = new URL(targetHref, window.location.href);
    const pageNum = Math.max(1, Number(pageUrl.searchParams.get('page') || 1) || 1);
    const pagedOffersUrl = new URL(offersApiUrl, window.location.href);
    pagedOffersUrl.searchParams.set('page', String(pageNum));

    offers.setAttribute('aria-busy', 'true');
    try {
      const [detailRes, offersRes] = await Promise.all([
        apiRequest(detailUrl, { signal: activeController.signal, cache: 'no-store' }),
        apiRequest(pagedOffersUrl.href, { signal: activeController.signal, cache: 'no-store' }),
      ]);
      if (seq !== offerRefreshSequence) return false;
      if (!detailRes.ok || !offersRes.ok) {
        const err = detailRes.ok ? offersRes.errorInfo : detailRes.errorInfo;
        if (err && notifyOnError) {
          notice(err.detail, true, err.isAuth ? buildLoginUrl() : null);
        }
        return false;
      }
      renderOffersSection(offers, detailRes.data, offersRes.data, pageNum, cfg);
      currentUrl = new URL(pageUrl.href);
      if (push) window.history.pushState(null, '', currentUrl.href);
      if (focusCount) {
        offers.querySelector('#offer-count')?.focus({ preventScroll: true });
      }
      return true;
    } catch (error) {
      if (error?.name === 'AbortError' && seq !== offerRefreshSequence) return false;
      return false;
    } finally {
      if (seq === offerRefreshSequence) offers.setAttribute('aria-busy', 'false');
    }
  }

  function applyOptimistic(type, form) {
    if (type === 'offer') {
      const card = form.closest('.request-board__card');
      const count = card?.querySelector('[data-offer-count]');
      const button = form.querySelector('button[type=submit]');
      if (!count || !button) return null;
      const previousCount = count.textContent;
      const previousButton = button.textContent;
      count.textContent = `${Number(count.dataset.offerCount || 0) + 1} penawaran`;
      button.textContent = 'Menyimpan…';
      return {
        rollback() {
          count.textContent = previousCount;
          button.textContent = previousButton;
        },
        uncertain() {
          button.textContent = previousButton;
        },
      };
    }

    if (type === 'delete') {
      const card = form.closest('.request-board__card');
      if (!card) return null;
      const marker = document.createComment('pending deletion');
      const details = card.querySelector('.request-board__delete');
      if (details) details.dataset.removing = 'true';
      card.after(marker);
      form.closest('dialog')?.close();
      if (details) details.open = false;
      card.remove();
      return {
        rollback() {
          if (details) delete details.dataset.removing;
          marker.replaceWith(card);
        },
        uncertain() {},
      };
    }

    if (type === 'fulfill') {
      const cfg = getConfig();
      const card = form.closest('.request-board__card');
      const badge = card?.querySelector('.request-board__badge');
      const actions = card?.querySelector('.request-board__owner-actions');
      const label = badge && [...badge.childNodes].findLast((node) => node.nodeType === Node.TEXT_NODE);
      if (!card || !badge || !label) return null;
      const previousLabel = label.textContent;
      const previousCardClass = card.className;
      const previousBadgeClass = badge.className;
      const badgeImg = badge.querySelector('.request-board__status-icon img');
      const previousIconSrc = badgeImg?.getAttribute('src') || '';
      const mobileSource = badge.querySelector('.request-board__status-icon source');
      const mobileParent = mobileSource?.parentElement;
      const mobileNext = mobileSource?.nextSibling;
      card.className = previousCardClass.replace(/request-board__card--\w+/, 'request-board__card--fulfilled');
      badge.className = 'request-board__badge request-board__badge--fulfilled';
      label.textContent = ' Terpenuhi';
      if (badgeImg) badgeImg.setAttribute('src', `${cfg.staticIconBase}fulfilled.svg`);
      mobileSource?.remove();
      if (actions) actions.inert = true;
      return {
        rollback() {
          card.className = previousCardClass;
          badge.className = previousBadgeClass;
          label.textContent = previousLabel;
          if (badgeImg && previousIconSrc) badgeImg.setAttribute('src', previousIconSrc);
          if (mobileSource && mobileParent) mobileParent.insertBefore(mobileSource, mobileNext || null);
          if (actions) actions.inert = false;
        },
        uncertain() {
          label.textContent = ' Hasil belum pasti';
        },
      };
    }

    if (type === 'selection') {
      const row = form.closest('.request-board__donor');
      const name = row?.querySelector('.request-board__donor-name')?.textContent.trim();
      if (!row || !name) return null;
      const selected = [...offers.querySelectorAll('.request-board__donor--selected')];
      const oldBanner = offers.querySelector('[data-offer-selection]');
      const oldLabels = [...offers.querySelectorAll('.request-board__offer-selected')];
      const pagination = offers.querySelector('.request-board__offer-pagination');
      const paginationWasInert = pagination?.inert;
      if (pagination) pagination.inert = true;
      const otherButtons = [...offers.querySelectorAll('button[type=submit]')].filter((button) => !form.contains(button));
      const buttonStates = otherButtons.map((button) => button.disabled);
      otherButtons.forEach((button) => { button.disabled = true; });
      selected.forEach((item) => item.classList.remove('request-board__donor--selected'));
      oldLabels.forEach((label) => { label.hidden = true; });
      row.classList.add('request-board__donor--selected');
      const banner = document.createElement('div');
      banner.className = 'request-board__offer-banner';
      banner.tabIndex = -1;
      banner.dataset.offerSelection = '';
      banner.textContent = `Penawar terpilih: ${name} `;
      const status = document.createElement('span');
      status.textContent = 'Menyimpan…';
      banner.append(status);
      oldBanner?.replaceWith(banner) || offers.querySelector('.request-board__offers-list')?.before(banner);
      return {
        rollback() {
          row.classList.remove('request-board__donor--selected');
          selected.forEach((item) => item.classList.add('request-board__donor--selected'));
          oldLabels.forEach((label) => { label.hidden = false; });
          if (oldBanner) banner.replaceWith(oldBanner);
          else banner.remove();
          otherButtons.forEach((button, index) => { button.disabled = buttonStates[index]; });
          if (pagination) pagination.inert = paginationWasInert;
        },
        uncertain() {
          banner.textContent = 'Hasil pemilihan belum dapat dipastikan.';
        },
      };
    }

    return null;
  }

  function enhanceCreateDialog(modal = false) {
    const dialog = modal ? document.querySelector('[data-create-dialog]') : createForm?.closest('.request-board');
    let form = modal ? null : createForm;
    let pending = document.querySelector('[data-create-pending]');
    if (modal && !pending && results) {
      pending = document.createElement('div');
      pending.dataset.createPending = '';
      pending.dataset.createCardSlot = '';
      pending.setAttribute('role', 'status');
      pending.setAttribute('aria-live', 'polite');
      pending.hidden = true;
      results.before(pending);
    }
    let attempt = null;
    let inFlight = false;
    let checkingStatus = false;
    let activeOperation = null;
    let activeTrigger = null;
    let opening = false;
    let submit = form?.querySelector('button[type=submit]');
    let recovery = null;
    let recoveryTimer = null;
    let submitLabel = submit?.textContent.trim() || 'Kirim Permintaan';
    const cardTemplate = modal ? document.querySelector('[data-create-card-template]') : null;

    function retryExpired(current = attempt) {
      if (!current) return false;
      const elapsedWall = Date.now() - current.startedAt;
      const elapsedMono = performance.now() - current.startedMonotonic;
      return current.expired || Math.max(elapsedWall, elapsedMono) >= CREATE_RETRY_WINDOW_MS;
    }

    function blockExpiredRetry() {
      if (!attempt || attempt.deleted || !retryExpired()) return false;
      attempt.expired = true;
      attempt.uncertain = false;
      if (submit) submit.disabled = true;
      if (modal) setPendingCardState(attempt.localId, 'uncertain');
      showRecovery('Batas waktu percobaan ulang 30 hari telah berakhir. Draft ini tidak dapat dikirim ulang. Periksa status atau papan permintaan untuk memastikan hasil penyimpanan.');
      return true;
    }

    function newKey() {
      if (window.crypto?.randomUUID) return window.crypto.randomUUID();
      const bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function readDraft(currentForm) {
      const value = (name) => currentForm.elements.namedItem(name)?.value?.trim() || '';
      const unitField = currentForm.elements.namedItem('unit');
      const unitCode = unitField?.value || '';
      const unitLabel = unitField?.selectedOptions?.[0]?.textContent.trim() || UNIT_LABELS[unitCode] || unitCode;
      return {
        title: value('title'),
        foodItem: value('food_item'),
        quantity: value('quantity'),
        unitCode,
        unitLabel,
        expiresAt: value('expires_at'),
        location: value('location'),
        description: value('description'),
        ownerName: dialog?.dataset.createOwnerName || getConfig().currentUserName || '',
      };
    }

    function normalizeExpiresAt(rawValue) {
      if (!rawValue) return undefined;
      if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(rawValue)) return rawValue;
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(rawValue)) {
        return `${rawValue}+07:00`;
      }
      return rawValue;
    }

    function buildCreatePayload(currentForm) {
      const draft = readDraft(currentForm);
      const payload = {
        title: draft.title,
        food_item: draft.foodItem,
        quantity: draft.quantity,
        unit: draft.unitCode,
        description: draft.description,
        location: draft.location,
      };
      const expiresIso = normalizeExpiresAt(draft.expiresAt);
      if (expiresIso !== undefined) {
        payload.expires_at = expiresIso;
      }
      return { draft, payload, jsonBody: JSON.stringify(payload) };
    }

    function pendingCardFor(localId) {
      if (!pending || !localId) return null;
      return [...pending.querySelectorAll('[data-create-card]')]
        .find((card) => card.dataset.localId === localId) || null;
    }

    function showPendingCard(currentAttempt) {
      if (!modal || !pending || !cardTemplate?.content?.firstElementChild) return;
      if (pendingCardFor(currentAttempt.localId)) {
        setPendingCardState(currentAttempt.localId, 'saving');
        return;
      }
      const { draft } = currentAttempt;
      const card = cardTemplate.content.firstElementChild.cloneNode(true);
      card.dataset.localId = currentAttempt.localId;
      card.querySelector('[data-create-card-title]').textContent = draft.title;
      card.querySelector('[data-create-card-food]').textContent = draft.foodItem;
      const qtyNum = Number(draft.quantity);
      const qtyText = Number.isFinite(qtyNum) ? qtyNum.toFixed(2) : draft.quantity;
      card.querySelector('[data-create-card-quantity]').textContent = `${qtyText} ${draft.unitLabel}`.trim();
      card.querySelector('[data-create-card-owner]').textContent = draft.ownerName;
      card.querySelector('[data-create-card-location]').textContent = draft.location;
      card.querySelector('[data-create-card-location-row]').hidden = !draft.location;
      card.querySelector('[data-create-card-description]').textContent = draft.description;
      card.querySelector('[data-create-card-description-row]').hidden = !draft.description;
      const expires = card.querySelector('[data-create-card-expires]');
      if (draft.expiresAt) {
        const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(draft.expiresAt);
        const formatted = match
          ? `${match[3]} ${MONTHS[Number(match[2]) - 1]} ${match[1]}, ${match[4]}:${match[5]}`
          : draft.expiresAt;
        expires.dateTime = draft.expiresAt;
        expires.textContent = `Batas waktu: ${formatted} WIB`;
        expires.hidden = false;
      } else {
        expires.hidden = true;
      }
      pending.prepend(card);
      pending.dataset.createCardActive = '';
      pending.hidden = false;
      setPendingCardState(currentAttempt.localId, 'saving');
    }

    function setPendingCardState(localId, state) {
      const card = pendingCardFor(localId);
      if (!card) return;
      const labels = {
        saving: 'Sedang menyimpan…',
        refreshing: 'Tersimpan · Memperbarui papan…',
        saved: 'Tersimpan di papan',
        uncertain: 'Belum terkonfirmasi',
      };
      const icons = {
        saving: 'pending',
        refreshing: 'pending',
        saved: 'pending',
        uncertain: 'pending',
      };
      card.dataset.state = state;
      card.querySelector('[data-create-card-state]').textContent = labels[state] || 'Sedang menyimpan…';
      card.dataset.stateIcon = icons[state] || 'pending';
      card.classList.toggle('request-board__card--pending', state !== 'uncertain');
    }

    function removePendingCard(localId) {
      const card = pendingCardFor(localId);
      if (!card || !pending) return;
      card.remove();
      if (!pending.querySelector('[data-create-card]')) {
        pending.hidden = true;
        delete pending.dataset.createCardActive;
      }
    }

    function resetPendingCard() {
      if (!modal || !pending) return;
      pending.replaceChildren();
      pending.hidden = true;
      delete pending.dataset.createCardActive;
    }

    function setFormDisabled(disabled) {
      if (!form) return;
      form.querySelectorAll('input:not([type=hidden]), select, textarea').forEach((field) => {
        field.disabled = disabled;
      });
      if (submit) {
        submit.disabled = disabled;
        submit.textContent = disabled ? 'Menyimpan…' : submitLabel;
      }
    }

    function showPending(message) {
      if (!pending) return;
      if (message && attempt) pending.dataset.localId = attempt.localId;
      else delete pending.dataset.localId;
      if (pending.hasAttribute('data-create-card-slot')) {
        if (!pending.querySelector('[data-create-card]')) {
          pending.textContent = '';
          pending.hidden = true;
        }
        return;
      }
      pending.textContent = message;
      pending.hidden = !message;
    }

    function hideRecovery() {
      recovery?.remove();
      recovery = null;
    }

    function focusInvalidOrRecovery() {
      if (!form) return;
      const invalid = form.querySelector('[aria-invalid="true"]:not(:disabled)');
      if (invalid) invalid.focus();
      else recovery?.focus();
    }

    function openCreateModal() {
      if (!modal || dialog.open || document.querySelector('dialog[open]:not([data-create-dialog])')) return false;
      dialog.showModal();
      return true;
    }

    function showRecovery(message, loginUrl = null, check = true) {
      if (!form) return;
      if (modal && !dialog.open) {
        if (openCreateModal()) focusInvalidOrRecovery();
      }
      hideRecovery();
      recovery = document.createElement('div');
      recovery.className = 'request-board__alert request-board__alert--error';
      recovery.dataset.createRecovery = '';
      recovery.setAttribute('role', 'alert');
      recovery.tabIndex = -1;
      recovery.append(document.createTextNode(message));
      if (check && attempt?.uncertain && !attempt?.deleted && !attempt?.expired) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn--tertiary btn--dark-green';
        button.textContent = 'Periksa status';
        button.addEventListener('click', checkAttemptStatus);
        recovery.append(' ', button);
      }
      const safeLogin = sanitizeSameOriginUrl(loginUrl);
      if (safeLogin) {
        const link = document.createElement('a');
        link.href = safeLogin;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Masuk di tab baru';
        recovery.append(' ', link);
      }
      form.before(recovery);
      if (!modal || dialog.open) focusInvalidOrRecovery();
    }

    function resetCreate() {
      window.clearTimeout(recoveryTimer);
      recoveryTimer = null;
      attempt = null;
      inFlight = false;
      checkingStatus = false;
      activeOperation = null;
      setFormDisabled(false);
      showPending('');
      hideRecovery();
    }

    function validateClient(currentForm) {
      const errors = {};
      for (const field of currentForm.elements) {
        if (!field.name || field.type === 'hidden') continue;
        const val = field.value.trim();
        if (field.name === 'title') {
          if (!val) errors.title = ['Judul permintaan wajib diisi.'];
          else if (val.length > 120) errors.title = ['Judul maksimal 120 karakter.'];
        } else if (field.name === 'food_item') {
          if (!val) errors.food_item = ['Bahan makanan wajib diisi.'];
          else if (val.length > 120) errors.food_item = ['Bahan makanan maksimal 120 karakter.'];
        } else if (field.name === 'quantity') {
          if (!val) errors.quantity = ['Jumlah wajib diisi.'];
          else if (!/^\d+(\.\d{1,2})?$/.test(val) || Number(val) < 0.01) {
            errors.quantity = ['Jumlah minimal 0.01 dengan maksimal 2 angka desimal.'];
          } else {
            const digitsOnly = val.replace('.', '').replace(/^0+/, '') || '0';
            if (digitsOnly.length > 8) errors.quantity = ['Jumlah maksimal 8 digit total.'];
          }
        } else if (field.name === 'unit' && !val) {
          errors.unit = ['Satuan wajib dipilih.'];
        } else if (field.name === 'description' && val.length > 1000) {
          errors.description = ['Deskripsi maksimal 1.000 karakter.'];
        } else if (field.name === 'location' && val.length > 120) {
          errors.location = ['Lokasi maksimal 120 karakter.'];
        } else if (field.name === 'expires_at') {
          if (!val && (field.required || field.getAttribute('aria-required') === 'true')) {
            errors.expires_at = ['Kolom ini wajib diisi.'];
          } else if (val) {
            const parsed = new Date(normalizeExpiresAt(val));
            if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
              errors.expires_at = ['Batas waktu harus di masa depan.'];
            }
          }
        }
      }
      if (!Object.keys(errors).length) return true;
      showErrors(currentForm, errors);
      announce('Formulir memiliki kesalahan. Periksa kolom yang ditandai.');
      return false;
    }

    async function completeConfirmedCreate(currentAttempt, successMessage = 'Permintaan berhasil dibuat.') {
      const { localId } = currentAttempt;
      if (modal) {
        const cardExists = Boolean(pendingCardFor(localId));
        if (!cardExists) showPendingCard(currentAttempt);
        setPendingCardState(localId, 'refreshing');
      }
      resetCreate();
      form.reset();
      if (modal && dialog.open) dialog.close();
      notice(successMessage, false);
      announce(successMessage);
      if (modal && results) {
        const refreshed = await boardGet(new URL(currentUrl), false, false, false);
        if (!refreshed) {
          setPendingCardState(localId, 'saved');
          notice('Permintaan berhasil dibuat, tetapi papan gagal diperbarui.', true);
        } else {
          removePendingCard(localId);
        }
      }
    }

    async function checkAttemptStatus() {
      if (!attempt || checkingStatus || attempt.deleted) return;
      if (attempt.expired) {
        blockExpiredRetry();
        return;
      }
      const cfg = getConfig();
      const checkedAttempt = attempt;
      const key = checkedAttempt.key;
      const statusUrl = cfg.apiCreationStatusUrl(key);
      if (!statusUrl) return;
      checkingStatus = true;
      try {
        const res = await apiRequest(statusUrl, { cache: 'no-store' });
        if (attempt !== checkedAttempt || attempt.key !== key) return;
        if (res.status === 403 || res.status === 401) {
          showRecovery(
            'Hasil penyimpanan belum dapat dipastikan. Masuk kembali di tab lain, lalu periksa status.',
            buildLoginUrl(cfg.createUrl),
          );
          return;
        }
        if (res.status === 404) {
          if (blockExpiredRetry()) return;
          showRecovery(
            'Status penyimpanan belum ditemukan (permintaan belum tersimpan, akun berbeda, atau kunci sudah lewat masa retensi). Periksa papan permintaan terlebih dahulu, atau coba simpan ulang dengan kunci yang sama.',
          );
          return;
        }
        if (!res.ok) throw new Error('status unavailable');
        if (res.data?.state === 'created' && res.data?.request_id) {
          await completeConfirmedCreate(checkedAttempt, 'Permintaan berhasil dibuat.');
          return;
        }
        if (res.data?.state === 'deleted') {
          attempt.deleted = true;
          attempt.uncertain = false;
          if (modal) removePendingCard(checkedAttempt.localId);
          showRecovery('Permintaan telah dibuat tetapi kemudian dihapus. Jangan kirim ulang draft ini.', null, false);
          if (submit) submit.disabled = true;
          return;
        }
        if (blockExpiredRetry()) return;
        showRecovery('Hasil penyimpanan belum dapat dipastikan. Jangan tutup atau muat ulang halaman; periksa status atau coba simpan lagi dengan kunci yang sama.');
      } catch {
        if (attempt !== checkedAttempt || attempt.key !== key) return;
        if (blockExpiredRetry()) return;
        showRecovery('Hasil penyimpanan belum dapat dipastikan. Jangan tutup atau muat ulang halaman; periksa status atau coba simpan lagi dengan kunci yang sama.');
      } finally {
        checkingStatus = false;
      }
    }

    function scheduleStatusReconcile() {
      window.clearTimeout(recoveryTimer);
      if (!attempt?.uncertain || attempt.deleted || attempt.expired) return;
      recoveryTimer = window.setTimeout(() => {
        if (navigator.onLine !== false && attempt?.uncertain && !inFlight && !checkingStatus) {
          checkAttemptStatus();
        }
      }, 2500);
    }

    async function handleSubmit(event) {
      event.preventDefault();
      if (inFlight || checkingStatus || attempt?.deleted || attempt?.wrongOwner) return;
      if (blockExpiredRetry()) return;
      if (pendingMutations.size) {
        showRecovery('Tunggu perubahan lain selesai disimpan.', null, Boolean(attempt?.uncertain));
        return;
      }
      clearErrors(form);
      hideRecovery();
      const cfg = getConfig();
      const apiRequestsUrl = cfg.apiRequestsUrl;
      if (!apiRequestsUrl) {
        form.submit();
        return;
      }

      const wasUncertain = Boolean(attempt?.uncertain);
      if (!wasUncertain && !validateClient(form)) return;
      if (!attempt) {
        const built = buildCreatePayload(form);
        attempt = {
          key: newKey(),
          localId: `temp-${newKey()}`,
          draft: built.draft,
          payload: built.payload,
          jsonBody: built.jsonBody,
          ownerId: cfg.currentUserId || dialog?.dataset.createOwner || '',
          uncertain: false,
          deleted: false,
          wrongOwner: false,
          expired: false,
          refreshCsrf: false,
          startedAt: Date.now(),
          startedMonotonic: performance.now(),
        };
      }

      const currentAttempt = attempt;
      const operation = { attempt: currentAttempt };
      activeOperation = operation;
      inFlight = true;
      setFormDisabled(true);
      if (modal) {
        showPendingCard(currentAttempt);
        showPending(`Menyimpan "${currentAttempt.draft.title}"…`);
        if (dialog.open) dialog.close();
      } else {
        showPending(`Menyimpan "${currentAttempt.draft.title}"…`);
      }

      let refreshFormToken = false;
      let retryNext = false;
      try {
        if (wasUncertain && currentAttempt.refreshCsrf) {
          const fresh = await timedFetch(form.action, { credentials: 'same-origin', cache: 'no-store' });
          if (!fresh.ok || fresh.redirected) {
            throw new Error('session unavailable');
          }
          const freshDoc = new DOMParser().parseFromString(await fresh.text(), 'text/html');
          const freshOwner = freshDoc.querySelector('[data-create-owner]')?.dataset.createOwner || '';
          if (freshOwner && currentAttempt.ownerId && freshOwner !== currentAttempt.ownerId) {
            currentAttempt.wrongOwner = true;
            if (modal) removePendingCard(currentAttempt.localId);
            showPending('');
            showRecovery('Hasil penyimpanan belum dapat dipastikan. Masuk kembali dengan akun pembuat draft, lalu periksa status.');
            if (submit) submit.disabled = true;
            return;
          }
          const token = freshDoc.querySelector('[data-board-create] [name=csrfmiddlewaretoken]');
          if (token?.value && form.elements.csrfmiddlewaretoken) {
            form.elements.csrfmiddlewaretoken.value = token.value;
          }
          currentAttempt.refreshCsrf = false;
        }

        // A suspended tab or slow CSRF refresh may cross the retention limit while awaited.
        if (blockExpiredRetry()) return;

        const res = await apiRequest(apiRequestsUrl, {
          method: 'POST',
          form,
          headers: { 'Idempotency-Key': currentAttempt.key },
          jsonBody: currentAttempt.payload,
          body: currentAttempt.jsonBody,
        });

        if (activeOperation !== operation) return;

        if (res.ok && res.data?.id) {
          const msg = res.status === 200 ? 'Permintaan sudah tersimpan sebelumnya.' : 'Permintaan berhasil dibuat.';
          await completeConfirmedCreate(currentAttempt, msg);
          return;
        }

        const err = res.errorInfo || normalizeApiError(res.status, res.data, res.isJson, res.rawText, res.redirected);

        if (err.isUncertain) {
          throw new Error('outcome unknown');
        }

        if (res.status === 409) {
          if (err.code === 'idempotency_deleted') {
            currentAttempt.deleted = true;
            currentAttempt.uncertain = false;
            if (modal) removePendingCard(currentAttempt.localId);
            showPending('');
            showRecovery(err.detail || 'Permintaan telah dibuat tetapi kemudian dihapus. Jangan kirim ulang draft ini.', null, false);
            if (submit) submit.disabled = true;
            return;
          }
          if (err.code === 'idempotency_conflict') {
            currentAttempt.uncertain = false;
            if (modal) removePendingCard(currentAttempt.localId);
            showPending('');
            showRecovery(err.detail || 'Data draft tidak cocok dengan permintaan yang sebelumnya dikirim pada kunci ini.', null, false);
            return;
          }
          currentAttempt.uncertain = false;
          if (modal) removePendingCard(currentAttempt.localId);
          showPending('');
          showRecovery(err.detail, null, false);
          return;
        }

        if (res.status === 400) {
          if (wasUncertain) throw new Error('outcome unknown');
          attempt = null;
          if (modal) removePendingCard(currentAttempt.localId);
          showPending('');
          if (modal && !dialog.open) openCreateModal();
          if (err.hasFields) {
            const mapped = { ...err.fields };
            if (err.nonFieldErrors.length) mapped.__all__ = err.nonFieldErrors;
            showErrors(form, mapped);
          } else {
            notice(err.detail, true);
          }
          if (modal) focusInvalidOrRecovery();
          return;
        }

        if (res.status === 403 || res.status === 401) {
          if (err.isCsrf) currentAttempt.refreshCsrf = true;
          refreshFormToken = err.isCsrf;
          if (wasUncertain) {
            if (err.isAuth) {
              showRecovery(
                err.detail || 'Sesi berakhir. Masuk di tab baru, lalu periksa status atau coba simpan lagi.',
                buildLoginUrl(cfg.createUrl),
              );
              return;
            }
            if (modal) showRecovery(err.detail, null, true);
            else notice(err.detail, true);
            return;
          }
          attempt = currentAttempt;
          currentAttempt.uncertain = err.isCsrf;
          if (modal) removePendingCard(currentAttempt.localId);
          showPending('');
          const loginHref = err.isAuth ? buildLoginUrl(cfg.createUrl) : null;
          if (modal) showRecovery(err.detail, loginHref, err.isCsrf);
          else notice(err.detail, true, loginHref);
          return;
        }

        throw new Error('outcome unknown');
      } catch {
        if (activeOperation !== operation) return;
        currentAttempt.uncertain = true;
        if (modal) setPendingCardState(currentAttempt.localId, 'uncertain');
        showPending('');
        if (blockExpiredRetry()) return;
        retryNext = true;
        showRecovery('Hasil penyimpanan belum dapat dipastikan. Sedang memeriksa status; jangan tutup atau muat ulang halaman.');
        scheduleStatusReconcile();
      } finally {
        if (activeOperation !== operation) return;
        inFlight = false;
        activeOperation = null;
        if (form) {
          setFormDisabled(false);
          if (submit) {
            submit.textContent = (retryNext || attempt?.uncertain) && !attempt?.deleted && !attempt?.wrongOwner ? 'Coba simpan lagi' : submitLabel;
            submit.disabled = Boolean(attempt?.deleted || attempt?.wrongOwner || retryExpired());
          }
          if (refreshFormToken) {
            timedFetch(form.action, { credentials: 'same-origin', cache: 'no-store' })
              .then(async (fresh) => {
                if (!fresh.ok || fresh.redirected) return;
                const freshDoc = new DOMParser().parseFromString(await fresh.text(), 'text/html');
                const token = freshDoc.querySelector('[data-board-create] [name=csrfmiddlewaretoken]');
                if (token?.value && form.elements.csrfmiddlewaretoken) {
                  form.elements.csrfmiddlewaretoken.value = token.value;
                }
              })
              .catch(() => {});
          }
        }
      }
    }

    function wireForm(boundForm) {
      form = boundForm;
      submit = form.querySelector('button[type=submit]');
      submitLabel = submit?.textContent.trim() || 'Kirim Permintaan';
      const draftKey = new Map();
      function uniqueId(prefix, existing) {
        let candidate = existing ? `${prefix}-${existing}` : prefix;
        while (document.getElementById(candidate)) candidate += '-dialog';
        return candidate;
      }
      if (modal) {
        const card = form.closest('.request-board__form-card');
        const mapping = new Map();
        card.querySelectorAll('[id]').forEach((element) => {
          const next = uniqueId('create-dialog', element.id);
          mapping.set(element.id, next);
          element.id = next;
        });
        card.querySelectorAll('[for], [aria-describedby], [aria-labelledby], a[href^="#"]').forEach((element) => {
          for (const name of ['for', 'aria-describedby', 'aria-labelledby']) {
            const value = element.getAttribute(name);
            if (!value) continue;
            element.setAttribute(name, value.split(/\s+/).map((item) => mapping.get(item) || item).join(' '));
          }
          const href = element.getAttribute('href');
          if (href?.startsWith('#') && mapping.has(href.slice(1))) {
            element.setAttribute('href', `#${mapping.get(href.slice(1))}`);
          }
        });
        const cancelLink = form.querySelector('.request-board__form-actions a');
        cancelLink?.addEventListener('click', (event) => {
          event.preventDefault();
          if (attempt?.uncertain && !attempt.deleted) {
            dialog.close();
            return;
          }
          resetCreate();
          dialog.close();
        });
      }
      form.querySelectorAll('input:not([type=hidden]), select, textarea').forEach((field) => {
        draftKey.set(field, field.value);
        field.addEventListener('input', () => {
          if (field.getAttribute('aria-invalid') === 'true') {
            field.removeAttribute('aria-invalid');
          }
        });
      });
      form.addEventListener('submit', handleSubmit);
    }

    if (!modal && form) wireForm(form);

    window.addEventListener('beforeunload', (event) => {
      if (!inFlight && !attempt?.uncertain) return;
      event.preventDefault();
      event.returnValue = '';
    });
    window.addEventListener('online', () => {
      if (attempt?.uncertain && !inFlight && !checkingStatus) {
        checkAttemptStatus();
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !inFlight && !checkingStatus && !attempt?.deleted) {
        blockExpiredRetry();
      }
    });

    if (modal) {
      results?.addEventListener('board:results-replaced', (event) => {
        if (!Array.isArray(event.detail)) return;
        event.detail.forEach(removePendingCard);
      });

      dialog?.addEventListener('click', (event) => {
        if (event.target === dialog && !inFlight) dialog.close();
      });
      dialog?.addEventListener('close', () => {
        if (activeTrigger?.isConnected) activeTrigger.focus({ preventScroll: true });
      });

      async function openFromLink(link) {
        if (opening || document.querySelector('dialog[open]:not([data-create-dialog])')) return;
        activeTrigger = link;
        if (!form) {
          opening = true;
          try {
            const response = await timedFetch(link.href, { credentials: 'same-origin' });
            if (!response.ok || response.redirected) throw new Error('form unavailable');
            const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
            const card = doc.querySelector('.request-board__form-card');
            const loadedForm = card?.querySelector('[data-board-create]');
            if (!card || !loadedForm) throw new Error('form missing');
            const closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'btn btn--tertiary btn--dark-green request-board__dialog-close';
            closeBtn.setAttribute('aria-label', 'Tutup formulir');
            closeBtn.textContent = 'Tutup';
            closeBtn.addEventListener('click', () => { if (!inFlight) dialog.close(); });
            card.prepend(closeBtn);
            dialog.replaceChildren(card);
            wireForm(loadedForm);
          } catch {
            window.location.assign(link.href);
            return;
          } finally {
            opening = false;
          }
        }
        if (openCreateModal()) {
          (attempt ? submit : form.elements.title)?.focus();
        }
      }

      document.addEventListener('click', (event) => {
        const link = event.target.closest('a');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const cfg = getConfig();
        const linkPath = new URL(link.href, window.location.href).pathname;
        const createPath = new URL(cfg.createUrl, window.location.href).pathname;
        if (linkPath !== createPath || !link.closest('.request-board__header-action, .request-board__empty-action')) return;
        event.preventDefault();
        openFromLink(link);
      });
    }
  }

  if (results && !createForm && document.querySelector('[data-create-dialog]')) {
    enhanceCreateDialog(true);
  } else if (createForm) {
    enhanceCreateDialog(false);
  }

  if (filter && results) {
    filter.addEventListener('input', (event) => {
      if (event.target?.name !== 'title' && event.target?.name !== 'q') return;
      window.clearTimeout(filterDebounceTimer);
      filterDebounceTimer = window.setTimeout(() => {
        const url = new URL(filter.action, window.location.href);
        url.search = '';
        for (const [key, value] of new FormData(filter).entries()) {
          if (value) url.searchParams.append(key, value);
        }
        boardGet(url);
      }, FILTER_DEBOUNCE_MS);
    });
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute('data-board-create')) return;
    if (!form.closest('.request-board')) return;

    const cfg = getConfig();
    if (form.matches('[data-filter-form]')) {
      event.preventDefault();
      window.clearTimeout(filterDebounceTimer);
      const url = new URL(form.action, window.location.href);
      url.search = '';
      for (const [key, value] of new FormData(form).entries()) {
        if (value) url.searchParams.append(key, value);
      }
      const updated = await boardGet(url);
      if (updated) {
        form.dispatchEvent(new CustomEvent('board:filter-applied', { detail: url.searchParams }));
        results?.querySelector('[data-board-results-focus]')?.focus();
      }
      return;
    }

    const cardAction = form.dataset.cardAction || '';
    const isOffer = Boolean(results) && (cardAction === 'offer' || form.action.endsWith('/offer/'));
    const isDelete = Boolean(results) && (cardAction === 'delete' || form.action.endsWith('/delete/'));
    const isFulfill = Boolean(results) && (cardAction === 'fulfill' || form.action.endsWith('/fulfill/'));
    const isSelection = Boolean(offers) && offers.contains(form);
    const optimisticMutation = isOffer || isDelete || isFulfill || isSelection;
    if (!optimisticMutation) return;

    event.preventDefault();
    if (form.dataset.pending) return;

    let endpoint = '';
    let method = 'POST';
    let jsonBody;
    let body;

    if (isOffer) {
      const requestId = form.closest('[data-request-id]')?.dataset.requestId;
      endpoint = cfg.apiOffersUrl(requestId);
      method = 'POST';
      jsonBody = {};
    } else if (isDelete) {
      const requestId = form.closest('[data-request-id]')?.dataset.requestId;
      endpoint = cfg.apiDetailUrl(requestId);
      method = 'DELETE';
      jsonBody = undefined;
      body = undefined;
    } else if (isFulfill) {
      const requestId = form.closest('[data-request-id]')?.dataset.requestId;
      endpoint = cfg.apiStatusUrl(requestId);
      method = 'PATCH';
      jsonBody = { status: 'fulfilled' };
    } else if (isSelection) {
      const offerIdMatch = form.action.match(/\/offers\/(\d+)\/select\/?$/);
      const offerId = Number(form.dataset.offerId || offerIdMatch?.[1]);
      endpoint = offers.dataset.apiSelectionUrl;
      method = 'PATCH';
      jsonBody = { offer_id: offerId };
    }

    if (!endpoint) {
      form.submit();
      return;
    }

    form.dataset.pending = 'true';
    const buttons = [...form.querySelectorAll('button[type=submit]')];
    buttons.forEach((button) => { button.disabled = true; });
    const card = form.closest('.request-board__card');
    const actionHost = isSelection ? offers : card;
    if (actionHost) actionHost.setAttribute('aria-busy', 'true');
    const mutationOwner = {};
    pendingMutations.add(mutationOwner);
    const optimistic = applyOptimistic(
      isOffer ? 'offer' : isDelete ? 'delete' : isFulfill ? 'fulfill' : 'selection',
      form,
    );
    if (isDelete && results) results.setAttribute('aria-busy', 'true');

    try {
      const res = await apiRequest(endpoint, {
        method,
        form,
        jsonBody,
        body,
      });

      if (!res.ok) {
        const err = res.errorInfo || normalizeApiError(res.status, res.data, res.isJson, res.rawText, res.redirected);
        if (err.isUncertain) throw new Error('outcome unknown');

        optimistic?.rollback();
        buttons.forEach((button) => { button.disabled = false; });

        const loginHref = err.isAuth ? buildLoginUrl() : null;
        notice(err.detail, true, loginHref);
        announce(err.detail);

        if (res.status === 409 || res.status === 404) {
          if (isSelection) await refreshOffers(currentUrl.href, false, false);
          else await boardGet(new URL(currentUrl), isDelete, false, false);
        }
        return;
      }

      const successMessage = isDelete
        ? 'Permintaan berhasil dihapus.'
        : isOffer
          ? (res.status === 200 ? 'Anda sudah mengirim penawaran untuk permintaan ini.' : 'Penawaran berhasil dikirim.')
          : isFulfill
            ? 'Permintaan berhasil ditandai terpenuhi.'
            : 'Penawar berhasil dipilih.';

      notice(successMessage, false);
      announce(successMessage);

      if (isSelection) {
        const refreshed = await refreshOffers(currentUrl.href, false, false, false);
        if (refreshed) {
          offers.querySelector('[data-offer-selection]')?.focus({ preventScroll: true });
        } else {
          notice('Aksi berhasil, tetapi daftar penawaran gagal diperbarui. Muat ulang halaman.', true);
        }
      } else {
        const refreshed = await boardGet(new URL(currentUrl), isDelete, false, false);
        if (isDelete && refreshed) {
          const focusEl = results.querySelector('[data-board-results-focus]')
            || document.querySelector('.request-board__header-action a, [data-filter-open]');
          focusEl?.focus({ preventScroll: true });
        } else if (!refreshed && isDelete) {
          notice('Permintaan berhasil dihapus, tetapi papan gagal diperbarui.', true);
        } else if (!refreshed) {
          notice('Perubahan tersimpan, tetapi tampilan papan gagal diperbarui. Muat ulang halaman bila perlu.', true);
        }
      }
    } catch {
      optimistic?.uncertain();
      const refreshed = isSelection
        ? await refreshOffers(currentUrl.href, false, false)
        : await boardGet(new URL(currentUrl), true, false, false);
      if (optimistic) {
        notice(
          refreshed
            ? 'Hasil perubahan belum dapat dipastikan. Papan telah diperbarui; periksa statusnya.'
            : 'Hasil perubahan belum dapat dipastikan. Periksa jaringan dan muat ulang papan sebelum mencoba lagi.',
          true,
        );
        if (!refreshed) {
          optimistic.rollback();
          buttons.forEach((button) => { button.disabled = false; });
        }
      } else {
        buttons.forEach((button) => { button.disabled = false; });
        notice('Gagal menghubungi server atau memperbarui tampilan. Periksa jaringan dan muat ulang sebelum mencoba lagi.', true);
      }
    } finally {
      pendingMutations.delete(mutationOwner);
      if (actionHost) actionHost.removeAttribute('aria-busy');
      if (isDelete && results) results.setAttribute('aria-busy', 'false');
      delete form.dataset.pending;
    }
  });

  if (results) {
    document.addEventListener('click', async (event) => {
      const link = event.target.closest('.request-board__pagination a, [data-board-reset]');
      if (!link || !link.closest('.request-board')) return;
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      window.clearTimeout(filterDebounceTimer);
      const reset = link.hasAttribute('data-board-reset');
      const updated = await boardGet(new URL(link.href, window.location.href));
      if (updated && !reset) {
        results.querySelector('[data-board-results-focus]')?.focus();
      } else if (updated && reset && filter) {
        filter.reset();
        for (const name of ['q', 'title', 'food_item', 'status', 'location', 'mine']) {
          if (filter.elements[name]) filter.elements[name].value = '';
        }
        if (filter.elements.sort) filter.elements.sort.value = 'newest';
        filter.dispatchEvent(new CustomEvent('board:filter-applied', { detail: new URLSearchParams() }));
        filter.elements.title?.focus({ preventScroll: true });
      }
    });
  }

  if (offers) {
    offers.addEventListener('click', async (event) => {
      const link = event.target.closest('.request-board__offer-pagination a');
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      await refreshOffers(link.href, true, true);
    });
  }

  window.addEventListener('popstate', async () => {
    currentUrl = new URL(window.location.href);
    if (results) {
      await boardGet(new URL(window.location.href), false, false, false);
      filter?.dispatchEvent(new CustomEvent('board:filter-applied', { detail: currentUrl.searchParams }));
    } else if (offers) {
      await refreshOffers(window.location.href, false, false);
    }
  });
})();
