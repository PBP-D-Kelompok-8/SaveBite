/* Enhance the server-rendered confirmation; native details remains the fallback. */
function enhanceBoardDeleteDialogs(root = document) {
  if (!window.HTMLDialogElement) return;
  root.querySelectorAll('.request-board__delete').forEach((details) => {
    if (details.dataset.enhanced) return;
    const trigger = details.querySelector('summary');
    const panel = details.querySelector('.request-board__delete-panel');
    if (!panel) return;
    details.dataset.enhanced = 'true';
    const dialog = document.createElement('dialog');
    dialog.className = panel.className;
    dialog.setAttribute('aria-labelledby', panel.querySelector('[id^=delete-label-]').id);
    dialog.setAttribute('aria-describedby', panel.querySelector('[id^=delete-description-]').id);
    while (panel.firstChild) dialog.appendChild(panel.firstChild);
    panel.replaceWith(dialog);
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      details.open = true;
      dialog.showModal();
      dialog.querySelector('.request-board__delete-cancel').focus();
    });
    dialog.querySelector('.request-board__delete-cancel').addEventListener('click', (event) => {
      event.preventDefault();
      dialog.close();
    });
    dialog.addEventListener('close', () => {
      details.open = false;
      if (!details.dataset.removing) trigger.focus({ preventScroll: true });
    });
  });
}
enhanceBoardDeleteDialogs();

/* Keep one GET form: inline on desktop, a native modal sheet on mobile. */
(() => {
  const form = document.querySelector('[data-filter-form]');
  const dialog = document.querySelector('#board-filter-dialog');
  if (!form || !dialog || typeof dialog.showModal !== 'function') return;
  const slot = document.querySelector('[data-filter-slot]');
  const toolbar = document.querySelector('.request-board__filter-toolbar');
  const trigger = document.querySelector('[data-filter-open]');
  const close = form.querySelector('[data-filter-close]');
  const mobile = window.matchMedia('(max-width: 768px)');
  let snapshot = [];
  let opened = false;

  function saveValues() {
    snapshot = [...form.elements].map(field => ({ field, value: field.value, checked: field.checked }));
  }
  function restoreValues() {
    snapshot.forEach(({ field, value, checked }) => {
      field.value = value;
      if (field.type === 'checkbox') field.checked = checked;
    });
  }
  form.addEventListener('board:filter-applied', (event) => {
    if (event.detail) {
      for (const name of ['title', 'food_item', 'status', 'location', 'sort', 'q', 'mine']) {
        const field = form.elements[name];
        if (field.type === 'checkbox') field.checked = event.detail.has(name);
        else field.value = event.detail.get(name) || '';
      }
    }
    saveValues();
    const summary = toolbar.querySelector('[data-filter-summary]');
    const status = form.elements.status;
    const sort = form.elements.sort;
    summary.textContent = `${status.selectedOptions[0].textContent} · ${sort.selectedOptions[0].textContent}`;
    if (form.elements.location.value) {
      const location = document.createElement('span');
      location.className = 'tw:block';
      location.textContent = form.elements.location.value;
      summary.appendChild(location);
    }
    for (const name of ['title', 'food_item', 'q', 'mine']) {
      const field = form.elements[name];
      if (!field.value) continue;
      const detail = document.createElement('span');
      detail.className = 'tw:block';
      detail.textContent = name === 'mine' ? 'Permintaan saya' : (name === 'food_item' ? field.selectedOptions[0].textContent : field.value);
      summary.appendChild(detail);
    }
    let indicator = trigger.querySelector('[aria-label="aktif"]');
    const active = ['title', 'food_item', 'status', 'location', 'q', 'mine'].some(name => form.elements[name].value) || form.elements.sort.value === 'oldest';
    if (active && !indicator) {
      indicator = document.createElement('span');
      indicator.className = 'request-board__filter-active';
      indicator.setAttribute('aria-label', 'aktif');
      trigger.appendChild(indicator);
    } else if (!active && indicator) indicator.remove();
    if (dialog.open) dialog.close();
    else if (opened) {
      opened = false;
      form.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.focus({ preventScroll: true });
    }
  });
  function syncLayout() {
    if (opened) restoreValues();
    opened = false;
    if (dialog.open) dialog.close();
    document.body.classList.remove('tw:overflow-hidden');
    trigger.setAttribute('aria-expanded', 'false');
    if (mobile.matches) {
      form.hidden = false;
      dialog.appendChild(form);
      trigger.setAttribute('aria-haspopup', 'dialog');
    } else {
      slot.appendChild(form);
      form.hidden = true;
      trigger.removeAttribute('aria-haspopup');
    }
    toolbar.hidden = false;
    close.hidden = !mobile.matches;
  }
  function open() {
    saveValues();
    opened = true;
    if (mobile.matches) {
      dialog.showModal();
      document.body.classList.add('tw:overflow-hidden');
    } else form.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    form.elements.title.focus();
  }
  trigger.addEventListener('click', () => {
    if (!opened) open();
    else if (dialog.open) dialog.close();
    else {
      restoreValues();
      opened = false;
      form.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
  form.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || mobile.matches || !opened) return;
    event.preventDefault();
    restoreValues();
    opened = false;
    form.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus({ preventScroll: true });
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    restoreValues();
    opened = false;
    trigger.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('tw:overflow-hidden');
    trigger.focus({ preventScroll: true });
  });
  mobile.addEventListener('change', syncLayout);
  syncLayout();
  form.dispatchEvent(new Event('board:filter-applied'));
  if (form.querySelector('.errorlist')) open();
})();

/* Server-rendered fragments and ordinary forms remain the source of truth. */
(() => {
  const results = document.querySelector('[data-board-results]');
  const createForm = document.querySelector('[data-board-create]');
  const offers = document.querySelector('[data-board-offers]');
  const filter = document.querySelector('[data-filter-form]');
  const boardStatus = document.querySelector('[data-board-status]');
  const filterStatus = document.querySelector('[data-filter-status]');
  if (!window.fetch || !window.AbortController || !window.URLSearchParams || !window.FormData || (!results && !createForm && !offers)) return;
  let controller = null;
  let sequence = 0;
  let currentUrl = new URL(window.location.href);
  let lastAnnouncement = "";
  let pendingMutation = false;
  let pendingSelection = false;
  const headers = { 'X-Requested-With': 'XMLHttpRequest' };

  async function timedFetch(url, options = {}) {
    const abort = new AbortController();
    const timer = window.setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch(url, { ...options, signal: abort.signal });
      const text = await response.text();
      return {
        ok: response.ok, status: response.status, redirected: response.redirected, headers: response.headers,
        text: async () => text,
        json: async () => JSON.parse(text),
      };
    }
    finally { window.clearTimeout(timer); }
  }

  function notice(message, isError = false, loginUrl = null) {
    const dialog = isError && document.querySelector('dialog:modal');
    if (!dialog && window.showRequestBoardToast) {
      const toast = window.showRequestBoardToast(message, isError, loginUrl);
      if (isError && createForm) toast.focus();
      return;
    }
    let region = document.querySelector('[data-inline-ajax-notice]');
    if (!region) {
      region = document.createElement('div');
      region.dataset.ajaxNotice = '';
      region.dataset.inlineAjaxNotice = '';
      region.className = 'request-board__alert';
      region.setAttribute('role', 'alert');
    }
    const container = dialog || document.querySelector('.request-board') || document.body;
    container.prepend(region);
    region.className = `request-board__alert request-board__alert--${isError ? 'error' : 'success'}`;
    region.textContent = message;
    if (loginUrl) {
      const link = document.createElement('a');
      link.href = loginUrl;
      if (dialog?.hasAttribute('data-create-dialog')) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
      link.textContent = ' Masuk';
      region.appendChild(link);
    }
    if (dialog) {
      region.setAttribute('tabindex', '-1');
      region.focus();
      dialog.addEventListener('close', () => {
        if (dialog.contains(region)) region.remove();
      }, { once: true });
    } else if (isError && createForm) {
      region.setAttribute('tabindex', '-1');
      region.focus();
    } else {
      region.removeAttribute('tabindex');
    }
  }

  function announce(message) {
    lastAnnouncement = message;
    if (filterStatus && filter && !filter.hidden && filter.closest('dialog[open]')) filterStatus.textContent = message;
    else if (boardStatus) boardStatus.textContent = message;
  }

  async function boardGet(url, correctPage = false, push = true) {
    if (pendingMutation) {
      announce('Tunggu perubahan selesai disimpan sebelum mengubah tampilan papan.');
      return false;
    }
    if (controller) controller.abort();
    controller = new AbortController();
    const activeController = controller;
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; activeController.abort(); }, 15000);
    const id = ++sequence;
    results.setAttribute('aria-busy', 'true');
    announce('Memuat permintaan.');
    try {
      let response = await fetch(url, { headers, signal: controller.signal, credentials: 'same-origin' });
      if (id !== sequence) return null;
      let page = Number(url.searchParams.get('page'));
      while (correctPage && response.status === 404 && Number.isSafeInteger(page) && page > 1) {
        page -= 1;
        if (page === 1) url.searchParams.delete('page');
        else url.searchParams.set('page', page);
        response = await fetch(url, { headers, signal: controller.signal, credentials: 'same-origin' });
        if (id !== sequence) return null;
      }
      if (!response.ok) {
        const message = response.status === 401 ? 'Silakan masuk untuk menggunakan filter ini.' : 'Filter atau halaman tidak valid.';
        announce(message);
        notice(message, true);
        return false;
      }
      const html = await response.text();
      if (id !== sequence) return null;
      results.innerHTML = html;
      enhanceBoardDeleteDialogs(results);
      const summary = results.querySelector('[data-board-result-summary]');
      if (summary) {
        const { total, page, pages } = summary.dataset;
        announce(Number(total) ? `${total} permintaan. Halaman ${page} dari ${pages}.` : 'Tidak ada permintaan yang cocok.');
      }
      currentUrl = url;
      if (push) history.pushState(null, '', url);
      else history.replaceState(null, '', url);
      return true;
    } catch (error) {
      if (id !== sequence || (error.name === 'AbortError' && !timedOut)) return null;
      if (id === sequence) {
        announce('Gagal memuat papan. Periksa jaringan dan coba lagi.');
        notice('Gagal memuat papan. Periksa jaringan dan coba lagi.', true);
      }
      return false;
    } finally {
      window.clearTimeout(timer);
      if (id === sequence) results.setAttribute('aria-busy', 'false');
    }
  }

  function fieldErrors(form, fields) {
    document.querySelector('.request-board__error-summary')?.remove();
    form.querySelectorAll('[data-ajax-field-error], .request-board__field-errors').forEach(element => element.remove());
    form.querySelectorAll('[aria-invalid]').forEach(element => element.removeAttribute('aria-invalid'));
    form.querySelectorAll('[aria-describedby]').forEach(element => {
      const ids = element.getAttribute('aria-describedby').split(/\s+/).filter(id => id && !id.startsWith('ajax-error-') && !id.endsWith('_error'));
      if (ids.length) element.setAttribute('aria-describedby', ids.join(' '));
      else element.removeAttribute('aria-describedby');
    });
    let firstInvalid = null;
    for (const [name, messages] of Object.entries(fields || {})) {
      const input = form.elements.namedItem(name);
      if (!input || !input.closest) {
        notice(messages.join(' '), true);
        continue;
      }
      input.setAttribute('aria-invalid', 'true');
      if (!firstInvalid) firstInvalid = input;
      const error = document.createElement('div');
      error.dataset.ajaxFieldError = '';
      error.className = 'request-board__field-errors';
      error.id = `ajax-error-${name}`;
      input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
      error.textContent = messages.join(' ');
      input.closest('.request-board__form-group').appendChild(error);
    }
    if (firstInvalid) firstInvalid.focus();
  }

  // Keep the original nodes for an exact rollback on an explicit rejection.
  function beginOffer(form) {
    const card = form.closest('.request-board__card');
    const count = card?.querySelector('[data-offer-count]');
    const button = form.querySelector('button[type=submit]');
    if (!count || !button) return null;
    const previousCount = count.textContent;
    const previousButton = button.textContent;
    count.textContent = `${Number(count.dataset.offerCount) + 1} penawaran`;
    button.textContent = 'Sedang menyimpan';
    announce('Penawaran sedang disimpan.');
    return {
      rollback() {
        count.textContent = previousCount;
        button.textContent = previousButton;
      },
      uncertain() {
        button.textContent = 'Hasil belum pasti';
      },
      confirmed() {
        button.textContent = 'Penawaran tersimpan';
      },
    };
  }

  function beginDelete(form) {
    const card = form.closest('.request-board__card');
    if (!card) return null;
    const marker = document.createComment('pending deletion');
    const details = form.closest('.request-board__delete');
    if (details) details.dataset.removing = 'true';
    form.closest('dialog')?.close();
    card.replaceWith(marker);
    announce('Permintaan sedang dihapus.');
    return {
      rollback() {
        marker.replaceWith(card);
        if (details) delete details.dataset.removing;
      },
      uncertain() {},
      confirmed() {},
    };
  }

  function beginFulfill(form) {
    const card = form.closest('.request-board__card');
    const badge = card?.querySelector('.request-board__badge');
    const actions = card?.querySelector('.request-board__owner-actions');
    const label = badge && [...badge.childNodes].findLast(node => node.nodeType === Node.TEXT_NODE);
    if (!card || !badge || !label) return null;
    const previousLabel = label.textContent;
    const previousCardClass = card.className;
    const previousBadgeClass = badge.className;
    card.classList.replace('request-board__card--offered', 'request-board__card--fulfilled');
    badge.classList.replace('request-board__badge--offered', 'request-board__badge--fulfilled');
    label.textContent = ' Terpenuhi (sedang menyimpan)';
    if (actions) actions.inert = true;
    announce('Status terpenuhi sedang disimpan.');
    return {
      rollback() {
        card.className = previousCardClass;
        badge.className = previousBadgeClass;
        label.textContent = previousLabel;
        if (actions) actions.inert = false;
      },
      uncertain() { label.textContent = ' Hasil belum pasti'; },
      confirmed() { label.textContent = ' Terpenuhi (tersimpan)'; },
    };
  }

  function beginSelection(form) {
    const row = form.closest('.request-board__donor');
    const name = row?.querySelector('.request-board__donor-name')?.textContent.trim();
    if (!row || !name) return null;
    const selected = [...offers.querySelectorAll('.request-board__donor--selected')];
    const oldBanner = offers.querySelector('[data-offer-selection]');
    const oldLabels = [...offers.querySelectorAll('.request-board__offer-selected')];
    const pagination = offers.querySelector('.request-board__offer-pagination');
    const paginationWasInert = pagination?.inert;
    if (pagination) pagination.inert = true;
    const otherButtons = [...offers.querySelectorAll('button[type=submit]')].filter(button => !form.contains(button));
    const buttonStates = otherButtons.map(button => button.disabled);
    otherButtons.forEach(button => { button.disabled = true; });
    selected.forEach(item => item.classList.remove('request-board__donor--selected'));
    row.classList.add('request-board__donor--selected');
    oldLabels.forEach(label => { label.hidden = true; });
    if (oldBanner) oldBanner.hidden = true;
    const banner = document.createElement('div');
    banner.className = 'request-board__offer-banner';
    banner.setAttribute('role', 'status');
    banner.textContent = `Penawar terpilih: ${name} (sedang menyimpan)`;
    offers.querySelector('.request-board__offers-list').before(banner);
    return {
      rollback() {
        banner.remove();
        if (oldBanner) oldBanner.hidden = false;
        oldLabels.forEach(label => { label.hidden = false; });
        row.classList.remove('request-board__donor--selected');
        selected.forEach(item => item.classList.add('request-board__donor--selected'));
        otherButtons.forEach((button, index) => { button.disabled = buttonStates[index]; });
        if (pagination) pagination.inert = paginationWasInert;
      },
      uncertain() { banner.textContent = 'Hasil pemilihan belum dapat dipastikan.'; },
      confirmed() { banner.textContent = `Penawar terpilih: ${name} (tersimpan)`; },
    };
  }

  async function refreshOffers() {
    try {
      const response = await timedFetch(window.location.href, { credentials: 'same-origin' });
      if (!response.ok || response.redirected) return false;
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const replacement = doc.querySelector('[data-board-offers]');
      if (!replacement) return false;
      offers.innerHTML = replacement.innerHTML;
      return true;
    } catch (error) {
      return false;
    }
  }

  // Load the existing server-rendered form into a native dialog; the link remains
  // an ordinary navigation when the required browser features are unavailable.
  function enhanceCreateDialog() {
    const modal = !!results;
    const dialog = modal ? document.querySelector('[data-create-dialog]') : createForm?.closest('.request-board');
    if (!dialog || (modal && !dialog.showModal) || !window.crypto?.getRandomValues) return;
    let pending = document.querySelector('[data-create-pending]');
    if (!pending) {
      pending = document.createElement('div');
      pending.dataset.createPending = '';
      pending.setAttribute('role', 'status');
      pending.setAttribute('aria-live', 'polite');
      pending.hidden = true;
      createForm.before(pending);
    }
    let form = null;
    let trigger = null;
    let attempt = null;
    let inFlight = false;
    let opening = false;
    let ownsMutation = false;
    let checkingStatus = false;
    let refreshFormToken = false;
    let recovery = null;
    let submit = null;
    let submitContents = [];

    function newKey() {
      if (window.crypto.randomUUID) return window.crypto.randomUUID();
      const bytes = window.crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function setLocked(locked) {
      form.querySelectorAll('input:not([type=hidden]), select, textarea').forEach(field => {
        field.disabled = locked;
      });
    }

    function pendingMessage(message) {
      pending.hidden = !message;
      pending.textContent = message;
      if (message && attempt) pending.dataset.localId = attempt.localId;
      else delete pending.dataset.localId;
    }

    function releaseMutation() {
      if (ownsMutation) {
        pendingMutation = false;
        ownsMutation = false;
      }
    }

    function showRecovery(message, loginUrl = null) {
      recovery.hidden = false;
      recovery.textContent = message + ' ';
      const check = document.createElement('button');
      check.type = 'button';
      check.className = 'btn btn--tertiary btn--dark-green';
      check.textContent = 'Periksa status';
      check.addEventListener('click', checkStatus);
      recovery.appendChild(check);
      if (loginUrl) {
        try {
          const url = new URL(loginUrl, window.location.href);
          if (url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol)) {
            const link = document.createElement('a');
            link.href = loginUrl;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = ' Masuk kembali';
            recovery.appendChild(link);
          }
        } catch (error) { /* Ignore an invalid recovery link. */ }
      }
    }

    async function completeCreate() {
      attempt = null;
      inFlight = false;
      releaseMutation();
      pendingMessage('');
      recovery.hidden = true;
      recovery.textContent = '';
      setLocked(false);
      submit.disabled = false;
      submit.replaceChildren(...submitContents.map(node => node.cloneNode(true)));
      form.reset();
      if (modal && dialog.open) dialog.close();
      if (!results) {
        notice('Permintaan bantuan makanan berhasil dibuat.');
        return;
      }
      const refreshed = await boardGet(new URL(currentUrl), true, false);
      if (refreshed === false) notice('Permintaan berhasil dibuat, tetapi papan gagal diperbarui. Muat ulang halaman.', true);
      else notice('Permintaan bantuan makanan berhasil dibuat.');
    }

    async function checkStatus() {
      if (!attempt || inFlight || checkingStatus) return;
      const key = attempt.key;
      checkingStatus = true;
      try {
        const url = new URL(`status/${key}/`, form.action);
        const response = await timedFetch(url, { headers: { ...headers, 'X-Create-Owner': dialog.dataset.createOwner }, credentials: 'same-origin', cache: 'no-store' });
        if (response.status === 401) {
          const auth = await response.json();
          attempt.refreshCsrf = true;
          if (attempt?.key === key) showRecovery('Hasil penyimpanan belum dapat dipastikan. Masuk kembali di tab lain, lalu periksa status.', auth.login_url);
          return;
        }
        if (response.status === 403) {
          const auth = await response.json();
          if (auth.code === 'owner_mismatch' && attempt?.key === key) {
            attempt.wrongOwner = true;
            attempt.refreshCsrf = true;
            submit.disabled = true;
            showRecovery('Hasil penyimpanan belum dapat dipastikan. Masuk kembali dengan akun pembuat draft, lalu periksa status.');
            return;
          }
        }
        if (!response.ok || response.redirected) throw new Error('status unavailable');
        const data = await response.json();
        if (!attempt || attempt.key !== key) return;
        attempt.wrongOwner = false;
        submit.disabled = false;
        if (data.ok && data.found && data.request_id) {
          await completeCreate();
          return;
        }
        if (data.ok && data.deleted) {
          attempt.deleted = true;
          pendingMessage('Permintaan telah dibuat, tetapi kemudian dihapus.');
          showRecovery('Permintaan telah dibuat tetapi kemudian dihapus. Jangan kirim ulang draft ini.');
          submit.disabled = true;
          return;
        }
      } catch (error) { /* A failed lookup cannot establish whether the POST committed. */ }
      finally { checkingStatus = false; }
      showRecovery('Hasil penyimpanan belum dapat dipastikan. Jangan tutup atau muat ulang halaman; periksa status atau coba simpan lagi dengan kunci yang sama.');
    }

    async function submitCreate(event) {
      event.preventDefault();
      if (inFlight || checkingStatus || attempt?.deleted || attempt?.wrongOwner) return;
      if (pendingMutation) {
        showRecovery('Tunggu perubahan lain selesai disimpan.');
        return;
      }
      if (!attempt) {
        const errors = {};
        for (const field of form.elements) {
          if (!field.name || !field.willValidate || field.validity.valid) continue;
          errors[field.name] = [field.validity.valueMissing ? 'Kolom ini wajib diisi.' : 'Masukkan nilai yang valid.'];
        }
        fieldErrors(form, errors);
        if (Object.keys(errors).length) return;
        attempt = { key: newKey(), localId: `temp-${newKey()}`, body: new FormData(form), title: form.elements.title.value, uncertain: false };
      }
      const currentAttempt = attempt;
      const wasUncertain = attempt.uncertain;
      inFlight = true;
      pendingMutation = true;
      ownsMutation = true;
      if (controller) controller.abort();
      ++sequence;
      results?.setAttribute('aria-busy', 'false');
      pendingMessage(`Sedang menyimpan: ${attempt.title}`);
      recovery.hidden = false;
      recovery.textContent = 'Sedang menyimpan permintaan.';
      setLocked(true);
      submit.disabled = true;
      try {
        if (refreshFormToken || (wasUncertain && attempt.refreshCsrf)) {
          const fresh = await timedFetch(form.action, { credentials: 'same-origin', cache: 'no-store' });
          if (!fresh.ok || fresh.redirected) throw new Error('session unavailable');
          const freshDoc = new DOMParser().parseFromString(await fresh.text(), 'text/html');
          const token = freshDoc.querySelector('[data-board-create] [name=csrfmiddlewaretoken]');
          if (!token?.value) throw new Error('csrf unavailable');
          attempt.body.set('csrfmiddlewaretoken', token.value);
          attempt.refreshCsrf = false;
          refreshFormToken = false;
        }
        const response = await timedFetch(form.action, {
          method: 'POST', headers: { ...headers, 'Idempotency-Key': attempt.key, 'X-Create-Owner': dialog.dataset.createOwner },
          body: attempt.body, credentials: 'same-origin',
        });
        if (response.redirected || response.status >= 500) throw new Error('outcome unknown');
        const json = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
        if (!response.ok) {
          if (wasUncertain) {
            if (response.status === 403) attempt.refreshCsrf = true;
            throw new Error('original outcome unknown');
          }
          if (response.status === 401 || response.status === 403) refreshFormToken = true;
          attempt = null;
          pendingMessage('');
          recovery.hidden = true;
          setLocked(false);
          if (json?.fields && Object.keys(json.fields).length) fieldErrors(form, json.fields);
          else notice(json?.message || 'Permintaan ditolak. Periksa sesi dan token CSRF.', true, json?.login_url);
          return;
        }
        if (!json?.ok || !json.request_id) throw new Error('outcome unknown');
        await completeCreate();
      } catch (error) {
        if (attempt !== currentAttempt) return;
        attempt.uncertain = true;
        pendingMessage(`Hasil belum pasti: ${attempt.title}`);
        submit.textContent = 'Coba simpan lagi';
        inFlight = false;
        releaseMutation();
        await checkStatus();
      } finally {
        if (!attempt || attempt === currentAttempt) {
          inFlight = false;
          releaseMutation();
          submit.disabled = !!(attempt?.deleted || attempt?.wrongOwner);
        }
      }
    }

    function prefixFormIds(card) {
      const mapping = new Map();
      card.querySelectorAll('[id]').forEach(element => {
        mapping.set(element.id, `create-modal-${element.id}`);
        element.id = mapping.get(element.id);
      });
      card.querySelectorAll('[for], [aria-describedby], [aria-labelledby], a[href^="#"]').forEach(element => {
        for (const name of ['for', 'aria-describedby', 'aria-labelledby']) {
          if (element.hasAttribute(name)) {
            element.setAttribute(name, element.getAttribute(name).split(/\s+/).map(id => mapping.get(id) || id).join(' '));
          }
        }
        const href = element.getAttribute('href');
        if (href?.startsWith('#') && mapping.has(href.slice(1))) element.setAttribute('href', `#${mapping.get(href.slice(1))}`);
      });
    }

    function setupForm(card) {
      form = card.querySelector('[data-board-create]');
      submit = form.querySelector('button[type=submit]');
      submitContents = [...submit.childNodes].map(node => node.cloneNode(true));
      recovery = document.createElement('div');
      recovery.dataset.createRecovery = '';
      recovery.setAttribute('role', 'status');
      recovery.hidden = true;
      form.prepend(recovery);
      form.addEventListener('submit', submitCreate);
      if (modal) form.querySelector('.request-board__form-actions a').addEventListener('click', event => {
        event.preventDefault();
        dialog.close();
      });
    }

    window.addEventListener('beforeunload', event => {
      if (!attempt || (!inFlight && !attempt.uncertain)) return;
      event.preventDefault();
      event.returnValue = '';
    });

    if (!modal) {
      setupForm(createForm.closest('.request-board__form-card'));
      return;
    }

    async function openCreate(link) {
      if (opening || dialog.open) return;
      opening = true;
      trigger = link;
      if (!form) {
        try {
          const response = await timedFetch(link.href, { credentials: 'same-origin' });
          if (!response.ok || response.redirected) throw new Error('form unavailable');
          const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
          const card = doc.querySelector('.request-board__form-card');
          if (!card) throw new Error('form unavailable');
          prefixFormIds(card);
          dialog.appendChild(card);
          setupForm(card);
        } catch (error) {
          opening = false;
          notice('Formulir tidak dapat dimuat. Periksa jaringan atau buka halaman formulir.', true);
          return;
        }
      }
      dialog.showModal();
      opening = false;
      (attempt ? submit : form.elements.title).focus();
    }

    document.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      // Compare with the actual server-rendered create link, not a hard-coded deployment prefix.
      const headerLink = document.querySelector('.request-board__header-action a');
      if (!headerLink || new URL(link.href).pathname !== new URL(headerLink.href).pathname || !headerLink.href.endsWith('/new/')) return;
      event.preventDefault();
      openCreate(link);
    });
    dialog.addEventListener('close', () => {
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    });
  }
  enhanceCreateDialog();

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (event.defaultPrevented || form === createForm) return;
    if (form === filter && results) {
      event.preventDefault();
      const url = new URL(form.action);
      url.search = new URLSearchParams(new FormData(form)).toString();
      // A newer draft supersedes any pending GET; both submit controls remain usable.
      if (await boardGet(url)) {
        const message = lastAnnouncement;
        const completedSequence = sequence;
        form.dispatchEvent(new CustomEvent('board:filter-applied', { detail: url.searchParams }));
        if (boardStatus) {
          boardStatus.textContent = '';
          requestAnimationFrame(() => { if (sequence === completedSequence) boardStatus.textContent = message; });
        }
      }
      return;
    }
    if (!((results && results.contains(form)) || (offers && offers.contains(form)))) return;
    if (form.method.toLowerCase() !== 'post') return;
    event.preventDefault();
    if (form.dataset.pending) return;
    const isOffer = !!results && form.action.endsWith('/offer/');
    const isDelete = !!results && form.action.endsWith('/delete/');
    const isFulfill = !!results && form.action.endsWith('/fulfill/');
    const isSelection = !!offers && offers.contains(form);
    const optimisticMutation = isOffer || isDelete || isFulfill || isSelection;
    if ((results && pendingMutation) || (isSelection && pendingSelection)) {
      announce('Tunggu perubahan selesai disimpan.');
      return;
    }
    form.dataset.pending = 'true';
    const body = new FormData(form);
    const buttons = [...form.querySelectorAll('button[type=submit]')];
    buttons.forEach(button => { button.disabled = true; });
    let optimistic = null;
    let keepDisabled = false;
    if (optimisticMutation) {
      if (isSelection) pendingSelection = true;
      else pendingMutation = true;
      if (controller) controller.abort();
      ++sequence;
      results?.setAttribute('aria-busy', 'false');
      optimistic = isSelection ? beginSelection(form) : isOffer ? beginOffer(form) : isDelete ? beginDelete(form) : beginFulfill(form);
    }
    try {
      const response = await timedFetch(form.action, { method: 'POST', headers, body, credentials: 'same-origin' });
      const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : {};
      if (optimisticMutation && (response.status >= 500 || response.redirected || (!data.ok && response.ok))) {
        throw new Error('outcome unknown');
      }
      if (!response.ok || !data.ok) {
        optimistic?.rollback();
        notice(data.message || (response.status === 403 ? 'Permintaan ditolak. Periksa sesi dan token CSRF.' : 'Permintaan gagal. Silakan coba lagi.'), true, data.login_url);
        return;
      }
      if (results) {
        optimistic?.confirmed();
        pendingMutation = false;
        if (controller) controller.abort();
        ++sequence;
        const refreshed = await boardGet(new URL(currentUrl), isDelete, false);
        if (refreshed !== false) notice(data.message);
        else if (refreshed === false) {
          keepDisabled = optimisticMutation;
          notice('Aksi berhasil, tetapi papan gagal diperbarui. Muat ulang halaman.', true);
        }
      } else {
        optimistic?.confirmed();
        if (await refreshOffers()) {
          notice(data.message);
          offers.querySelector('[data-offer-selection]')?.focus({ preventScroll: true });
        } else {
          keepDisabled = true;
          notice('Aksi berhasil, tetapi daftar penawaran gagal diperbarui. Muat ulang halaman.', true);
        }
      }
    } catch (error) {
      if (optimisticMutation) {
        optimistic?.uncertain();
        pendingMutation = false;
        const refreshed = isSelection ? await refreshOffers() : await boardGet(new URL(currentUrl), true, false);
        keepDisabled = refreshed !== true;
        notice(refreshed === true
          ? 'Hasil perubahan belum dapat dipastikan. Papan telah diperbarui; periksa statusnya.'
          : 'Hasil perubahan belum dapat dipastikan. Periksa jaringan dan muat ulang papan sebelum mencoba lagi.', true);
      } else {
        notice('Gagal menghubungi server atau memperbarui tampilan. Periksa jaringan dan muat ulang sebelum mencoba lagi.', true);
      }
    } finally {
      if (optimisticMutation) pendingMutation = false;
      if (isSelection) pendingSelection = false;
      delete form.dataset.pending;
      if (!keepDisabled) buttons.forEach(button => { button.disabled = false; });
    }
  });

  if (results) {
    document.addEventListener('click', async event => {
      const link = event.target.closest('.request-board__pagination a, [data-board-reset]');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const reset = link.hasAttribute('data-board-reset');
      const updated = await boardGet(new URL(link.href));
      if (updated && !reset) results.querySelector('[data-board-results-focus]')?.focus();
      if (updated && reset && filter) {
        for (const name of ['q', 'title', 'food_item', 'status', 'location', 'mine']) filter.elements[name].value = '';
        filter.elements.sort.value = 'newest';
        const message = lastAnnouncement;
        const completedSequence = sequence;
        filter.dispatchEvent(new Event('board:filter-applied'));
        if (boardStatus) {
          boardStatus.textContent = '';
          requestAnimationFrame(() => { if (sequence === completedSequence) boardStatus.textContent = message; });
        }
        document.querySelector('[data-filter-open]')?.focus({ preventScroll: true });
      }
    });
    window.addEventListener('popstate', () => window.location.reload());
  }
})();
