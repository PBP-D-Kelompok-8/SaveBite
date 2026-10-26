/* SaveBite — Form Modal
   Buka:  <button type="button" data-modal-open="#form-modal"
                  data-title="Edit Catatan" data-action="/log/3/edit/"
                  data-values='{"food_name":"Nasi Putih","quantity":2}'>
   Tutup: elemen apa pun dengan data-modal-close, tombol Esc, atau klik di luar kartu.

   Perilaku saat dibuka lewat tombol pemicu:
   - Judul & action SELALU di-reset ke default dialog, lalu ditimpa data-title / data-action
     kalau ada. Nilai dari pembukaan sebelumnya tidak pernah nyangkut.
   - Form di-reset, lalu diisi dari data-values (JSON { nama_field: nilai }) untuk mode Edit.
     Tanpa data-values = mode Tambah (form kosong).
   - Status error dari render server dibersihkan (kecuali dialog dibuka otomatis, lihat bawah).
   Dialog dengan atribut data-open-on-load terbuka sendiri saat halaman dimuat
   (dipakai untuk menampilkan ulang modal beserta error validasi dari server). */
(() => {
  const isBackdrop = (el) =>
    el instanceof HTMLDialogElement && el.classList.contains("modal");

  // Catat di mana pointer ditekan, supaya menyeleksi teks di dalam kartu lalu
  // melepas di backdrop tidak ikut menutup modal.
  let pressedOnBackdrop = false;
  document.addEventListener("pointerdown", (e) => {
    pressedOnBackdrop = isBackdrop(e.target);
  });

  function setTitle(dialog, opener) {
    const title = dialog.querySelector("[data-modal-title]");
    if (!title) return;
    title.dataset.defaultText ??= title.textContent.trim();
    title.textContent = opener.dataset.title || title.dataset.defaultText;
  }

  function setAction(form, opener) {
    form.dataset.defaultAction ??= form.getAttribute("action") || "";
    form.setAttribute("action", opener.dataset.action || form.dataset.defaultAction);
  }

  function clearErrors(form) {
    form.querySelectorAll(".input--incorrect").forEach((el) => el.classList.remove("input--incorrect"));
    form.querySelectorAll(".input__error").forEach((el) => el.remove());
    form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
  }

  function fillValues(form, opener) {
    form.reset();
    clearErrors(form);
    if (!opener.dataset.values) return;
    try {
      const values = JSON.parse(opener.dataset.values);
      for (const [key, val] of Object.entries(values)) {
        const field = form.elements[key];
        if (field && "value" in field) field.value = val ?? "";
      }
    } catch (err) {
      console.error("[form-modal] data-values bukan JSON yang valid:", err);
    }
  }

  function openModal(opener) {
    const dialog = document.querySelector(opener.dataset.modalOpen);
    if (!(dialog instanceof HTMLDialogElement)) {
      console.error(
        `[form-modal] Dialog "${opener.dataset.modalOpen}" tidak ditemukan. ` +
          `Sudah di-include components/form-modal.html di halaman ini?`
      );
      return;
    }
    const form = dialog.querySelector("form");
    setTitle(dialog, opener);
    if (form) {
      setAction(form, opener);
      fillValues(form, opener);
    }
    dialog.showModal();
    dialog.querySelector(".input__field")?.focus();
  }

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-modal-open]");
    if (opener) {
      openModal(opener);
      return;
    }

    const closer = e.target.closest("[data-modal-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }

    // Klik backdrop: target = <dialog> itu sendiri, dan pointer memang ditekan di backdrop.
    if (isBackdrop(e.target) && pressedOnBackdrop) {
      e.target.close();
    }
    pressedOnBackdrop = false;
  });

  // Cegah klik ganda Simpan (double submit). Submit yang gagal validasi browser tidak sampai sini.
  document.addEventListener("submit", (e) => {
    const submit = e.target.closest?.(".modal__form")?.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
  });
  // Tombol dipulihkan kalau halaman dimuat ulang dari cache (tombol Back).
  window.addEventListener("pageshow", () => {
    document
      .querySelectorAll('.modal__form [type="submit"]')
      .forEach((btn) => (btn.disabled = false));
  });

  // Buka otomatis (mis. setelah validasi server gagal).
  const openOnLoad = () =>
    document.querySelectorAll("dialog.modal[data-open-on-load]").forEach((d) => {
      if (!d.open) d.showModal();
    });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", openOnLoad);
  } else {
    openOnLoad();
  }
})();