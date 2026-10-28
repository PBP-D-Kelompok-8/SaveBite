/* Enhance the server-rendered confirmation; native details remains the fallback. */
(() => {
  if (!window.HTMLDialogElement) return;
  document.querySelectorAll('.request-board__delete').forEach((details) => {
    const trigger = details.querySelector('summary');
    const panel = details.querySelector('.request-board__delete-panel');
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
})();

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

  function saveValues() {
    snapshot = [...form.elements].map(field => ({ field, value: field.value, checked: field.checked }));
  }
  function syncLayout() {
    if (dialog.open) dialog.close();
    if (mobile.matches) dialog.appendChild(form);
    else slot.appendChild(form);
    toolbar.hidden = !mobile.matches;
    close.hidden = !mobile.matches;
  }
  function open() {
    saveValues();
    dialog.showModal();
    trigger.setAttribute('aria-expanded', 'true');
    document.body.classList.add('tw:overflow-hidden');
    close.focus();
  }
  trigger.addEventListener('click', open);
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    snapshot.forEach(({ field, value, checked }) => {
      field.value = value;
      if (field.type === 'checkbox') field.checked = checked;
    });
    trigger.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('tw:overflow-hidden');
    if (mobile.matches) trigger.focus({ preventScroll: true });
  });
  mobile.addEventListener('change', syncLayout);
  syncLayout();
  if (mobile.matches && form.querySelector('.errorlist')) open();
})();
