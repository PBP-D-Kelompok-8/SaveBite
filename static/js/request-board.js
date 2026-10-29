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
    dialog.setAttribute('aria-labelledby', panel.querySelector('strong').id);
    dialog.setAttribute('aria-describedby', panel.querySelector('p').id);
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
      trigger.focus({ preventScroll: true });
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
  if (!window.fetch || !window.AbortController || !window.URLSearchParams || !window.FormData || (!results && !createForm && !offers)) return;
  let controller = null;
  let sequence = 0;
  let currentUrl = new URL(window.location.href);
  const headers = { 'X-Requested-With': 'XMLHttpRequest' };

  function notice(message, isError = false, loginUrl = null) {
    let region = document.querySelector('[data-ajax-notice]');
    if (!region) {
      region = document.createElement('div');
      region.dataset.ajaxNotice = '';
      region.className = 'request-board__alert';
      region.setAttribute('role', 'alert');
    }
    const dialog = isError && document.querySelector('dialog:modal');
    const container = dialog || document.querySelector('.request-board') || document.body;
    container.prepend(region);
    region.className = `request-board__alert request-board__alert--${isError ? 'error' : 'success'}`;
    region.textContent = message;
    if (loginUrl) {
      const link = document.createElement('a');
      link.href = loginUrl;
      link.textContent = ' Masuk';
      region.appendChild(link);
    }
    if (dialog) {
      region.setAttribute('tabindex', '-1');
      region.focus();
      dialog.addEventListener('close', () => {
        if (dialog.contains(region)) region.remove();
      }, { once: true });
    } else {
      region.removeAttribute('tabindex');
    }
  }

  async function boardGet(url, correctPage = false, push = true) {
    if (controller) controller.abort();
    controller = new AbortController();
    const id = ++sequence;
    try {
      let response = await fetch(url, { headers, signal: controller.signal, credentials: 'same-origin' });
      if (id !== sequence) return false;
      let page = Number(url.searchParams.get('page'));
      while (correctPage && response.status === 404 && Number.isSafeInteger(page) && page > 1) {
        page -= 1;
        if (page === 1) url.searchParams.delete('page');
        else url.searchParams.set('page', page);
        response = await fetch(url, { headers, signal: controller.signal, credentials: 'same-origin' });
        if (id !== sequence) return false;
      }
      if (!response.ok) {
        notice(response.status === 401 ? 'Silakan masuk untuk menggunakan filter ini.' : 'Filter atau halaman tidak valid.', true);
        return false;
      }
      const html = await response.text();
      if (id !== sequence) return false;
      results.innerHTML = html;
      enhanceBoardDeleteDialogs(results);
      currentUrl = url;
      if (push) history.pushState(null, '', url);
      else history.replaceState(null, '', url);
      return true;
    } catch (error) {
      if (error.name !== 'AbortError') notice('Gagal memuat papan. Periksa jaringan dan coba lagi.', true);
      return false;
    }
  }

  function fieldErrors(form, fields) {
    document.querySelector('.request-board__error-summary')?.remove();
    form.querySelectorAll('[data-ajax-field-error], .request-board__field-errors').forEach(element => element.remove());
    form.querySelectorAll('[aria-invalid]').forEach(element => element.removeAttribute('aria-invalid'));
    form.querySelectorAll('[aria-describedby]').forEach(element => {
      const ids = element.getAttribute('aria-describedby').split(/\s+/).filter(id => id && !id.startsWith('ajax-error-'));
      if (ids.length) element.setAttribute('aria-describedby', ids.join(' '));
      else element.removeAttribute('aria-describedby');
    });
    for (const [name, messages] of Object.entries(fields || {})) {
      const input = form.elements.namedItem(name);
      if (!input || !input.closest) {
        notice(messages.join(' '), true);
        continue;
      }
      input.setAttribute('aria-invalid', 'true');
      const error = document.createElement('div');
      error.dataset.ajaxFieldError = '';
      error.className = 'request-board__field-errors';
      error.id = `ajax-error-${name}`;
      input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
      error.textContent = messages.join(' ');
      input.closest('.request-board__form-group').appendChild(error);
    }
  }

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form === filter && results) {
      event.preventDefault();
      const url = new URL(form.action);
      url.search = new URLSearchParams(new FormData(form)).toString();
      // A newer draft supersedes any pending GET; both submit controls remain usable.
      if (await boardGet(url)) form.dispatchEvent(new CustomEvent('board:filter-applied', { detail: url.searchParams }));
      return;
    }
    if (!(form === createForm || (results && results.contains(form)) || (offers && offers.contains(form)))) return;
    if (form.method.toLowerCase() !== 'post') return;
    event.preventDefault();
    if (form.dataset.pending) return;
    form.dataset.pending = 'true';
    const buttons = [...form.querySelectorAll('button[type=submit]')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(form.action, { method: 'POST', headers, body: new FormData(form), credentials: 'same-origin' });
      const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : {};
      if (!response.ok || !data.ok) {
        if (form === createForm && data.fields) fieldErrors(form, data.fields);
        notice(data.message || (response.status === 403 ? 'Permintaan ditolak. Periksa sesi dan token CSRF.' : 'Permintaan gagal. Silakan coba lagi.'), true, data.login_url);
        return;
      }
      if (form === createForm) {
        window.location.assign(data.redirect_url);
      } else if (results) {
        if (controller) controller.abort();
        ++sequence;
        const isDelete = form.action.endsWith('/delete/');
        if (await boardGet(new URL(currentUrl), isDelete, false)) notice(data.message);
        else notice('Aksi berhasil, tetapi papan gagal diperbarui. Muat ulang halaman.', true);
      } else {
        try {
          const refreshed = await fetch(window.location.href, { credentials: 'same-origin' });
          if (!refreshed.ok || refreshed.redirected) throw new Error('refresh failed');
          const doc = new DOMParser().parseFromString(await refreshed.text(), 'text/html');
          const replacement = doc.querySelector('[data-board-offers]');
          if (!replacement) throw new Error('refresh failed');
          offers.innerHTML = replacement.innerHTML;
          notice(data.message);
          offers.querySelector('[data-offer-selection]')?.focus({ preventScroll: true });
        } catch (error) {
          notice('Aksi berhasil, tetapi daftar penawaran gagal diperbarui. Muat ulang halaman.', true);
        }
      }
    } catch (error) {
      notice('Gagal menghubungi server atau memperbarui tampilan. Periksa jaringan dan muat ulang sebelum mencoba lagi.', true);
    } finally {
      delete form.dataset.pending;
      buttons.forEach(button => { button.disabled = false; });
    }
  });

  if (results) {
    document.addEventListener('click', async event => {
      const link = event.target.closest('.request-board__pagination a, [data-board-reset]');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const reset = link.hasAttribute('data-board-reset');
      if (await boardGet(new URL(link.href)) && reset && filter) {
        for (const name of ['q', 'title', 'food_item', 'status', 'location', 'mine']) filter.elements[name].value = '';
        filter.elements.sort.value = 'newest';
        filter.dispatchEvent(new Event('board:filter-applied'));
        document.querySelector('[data-filter-open]')?.focus({ preventScroll: true });
      }
    });
    window.addEventListener('popstate', () => window.location.reload());
  }
})();
