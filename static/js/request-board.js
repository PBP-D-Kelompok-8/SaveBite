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
