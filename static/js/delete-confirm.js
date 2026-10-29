/* SaveBite — Delete Confirmation
   Buka:  <button data-confirm-open="#delete-confirm" data-name="Nasi Putih" data-action="/log/3/hapus/">
   Tutup: elemen apa pun dengan data-confirm-close, tombol Esc, atau klik di luar kartu.

   Perilaku saat dibuka:
   - Nama & action SELALU di-reset dulu ke nilai default dialog (yang dirender server),
     lalu ditimpa data-name / data-action dari tombol pemicu kalau ada.
     Jadi nilai dari item sebelumnya tidak pernah nyangkut.
   - Kalau form method="post" tapi tidak ada action sama sekali, tombol submit
     di-disable (supaya tidak POST ke halaman saat ini) dan error dicetak ke console. */
(() => {
  const DEFAULT_NAME = "[nama item]";

  const isBackdrop = (el) =>
    el instanceof HTMLDialogElement && el.classList.contains("confirm");

  // Catat di mana pointer ditekan. Tanpa ini, menyeleksi teks di dalam kartu lalu
  // melepas di backdrop akan memicu click dengan target <dialog> dan menutupnya.
  let pressedOnBackdrop = false;
  document.addEventListener("pointerdown", (e) => {
    pressedOnBackdrop = isBackdrop(e.target);
  });

  function openConfirm(opener) {
    const dialog = document.querySelector(opener.dataset.confirmOpen);
    if (!dialog) {
      console.error(
        `[delete-confirm] Dialog "${opener.dataset.confirmOpen}" tidak ditemukan. ` +
          `Sudah di-include components/delete-confirm.html di halaman ini?`
      );
      return;
    }

    // Nama item
    const name = dialog.querySelector("[data-confirm-name]");
    if (name) {
      name.dataset.defaultText ??= name.textContent.trim() || DEFAULT_NAME;
      name.textContent = opener.dataset.name || name.dataset.defaultText;
    }

    // Action form
    const form = dialog.querySelector("form");
    if (form) {
      form.dataset.defaultAction ??= form.getAttribute("action") || "";
      const action = opener.dataset.action || form.dataset.defaultAction;
      form.setAttribute("action", action);

      const submit = form.querySelector('[type="submit"]');
      const needsAction = form.method !== "dialog"; // method="dialog" (demo) tidak butuh action
      const missing = needsAction && !action;
      if (submit) submit.disabled = missing;
      if (missing) {
        console.error(
          "[delete-confirm] Tidak ada action: isi data-action pada tombol pemicu " +
            "atau parameter `action` pada komponen."
        );
      }
    }

    dialog.showModal();
  }

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-confirm-open]");
    if (opener) {
      openConfirm(opener);
      return;
    }

    const closer = e.target.closest("[data-confirm-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }

    // Klik backdrop: target = elemen <dialog> itu sendiri, dan pointer
    // memang ditekan di backdrop (bukan drag dari dalam kartu).
    if (isBackdrop(e.target) && pressedOnBackdrop) {
      e.target.close();
    }
    pressedOnBackdrop = false;
  });
})();