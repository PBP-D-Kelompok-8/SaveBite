(() => {
  const REQUEST_HEADER = { "X-Requested-With": "XMLHttpRequest" };
  let latestListRequest = 0;
  let searchTimer;

  const getMessageRegion = () => document.querySelector("[data-foodlog-message]");

  function announce(message, isError = false) {
    const region = getMessageRegion();
    if (!region) return;
    region.textContent = message;
    region.classList.toggle("food-journal__live-message--error", isError);
  }

  async function readJsonResponse(response) {
    if (response.redirected && response.url.includes("/login")) {
      window.location.assign(response.url);
      throw new Error("Sesi login sudah berakhir.");
    }
    if (!(response.headers.get("content-type") || "").includes("application/json")) {
      throw new Error("Respons server tidak valid.");
    }
    return response.json();
  }

  function applyJournalPayload(payload) {
    const listFragment = document.createRange().createContextualFragment(payload.list_html);
    const summaryFragment = document.createRange().createContextualFragment(payload.summary_html);
    const oldResults = document.querySelector("[data-foodlog-results]");
    const oldSummary = document.querySelector(".food-journal__summary");
    const newSummary = summaryFragment.querySelector(".food-journal__summary");
    if (oldResults) {
      oldResults.replaceChildren(listFragment);
      oldResults.removeAttribute("aria-busy");
    }
    if (oldSummary && newSummary) oldSummary.replaceWith(newSummary);
  }

  function actionWithCurrentFilters(action) {
    const url = new URL(action, window.location.href);
    url.search = new URL(window.location.href).search;
    return url.href;
  }

  async function refreshJournal(url = window.location.href, announceResults = false) {
    const requestId = ++latestListRequest;
    const results = document.querySelector("[data-foodlog-results]");
    if (results) results.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(url, {
        headers: REQUEST_HEADER,
        credentials: "same-origin",
      });
      const payload = await readJsonResponse(response);
      if (!response.ok || !payload.ok || requestId !== latestListRequest) return;
      applyJournalPayload(payload);
      if (announceResults) announce("Daftar catatan diperbarui.");
    } catch (error) {
      if (requestId === latestListRequest) announce("Daftar gagal diperbarui. Coba lagi.", true);
    } finally {
      if (requestId === latestListRequest && results) results.removeAttribute("aria-busy");
    }
  }

  function filterUrl(form) {
    const url = new URL(form.action, window.location.href);
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(form)) {
      const cleaned = String(value).trim();
      if (cleaned) params.set(name, cleaned);
    }
    url.search = params.toString();
    return url;
  }

  async function submitFoodLogForm(form) {
    if (form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    const modal = form.closest("dialog");
    const submitButton = form.querySelector('[type="submit"]');
    const isEdit = Boolean(form.dataset.foodlogEdit);
    if (submitButton) submitButton.disabled = true;
    form.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(actionWithCurrentFilters(form.action), {
        method: "POST",
        body: new FormData(form),
        headers: REQUEST_HEADER,
        credentials: "same-origin",
      });
      const payload = await readJsonResponse(response);
      if (!response.ok || !payload.ok) {
        if (payload.form_html && modal) {
          modal.querySelector(".modal__content").innerHTML = payload.form_html;
          modal.querySelector(".input__field")?.focus();
        }
        return;
      }

      modal?.close();
      form.reset();
      delete form.dataset.foodlogEdit;
      delete form.dataset.foodLogId;
      latestListRequest += 1;
      applyJournalPayload(payload);
      announce(payload.message || (isEdit ? "Catatan diperbarui." : "Catatan ditambahkan."));
    } catch (error) {
      announce("Catatan gagal disimpan. Coba lagi.", true);
    } finally {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      if (submitButton) submitButton.disabled = false;
    }
  }

  async function submitDeleteForm(form) {
    if (form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    const submitButton = form.querySelector('[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    form.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(actionWithCurrentFilters(form.action), {
        method: "POST",
        body: new FormData(form),
        headers: REQUEST_HEADER,
        credentials: "same-origin",
      });
      const payload = await readJsonResponse(response);
      if (!response.ok || !payload.ok) throw new Error("Penghapusan gagal.");

      form.closest("dialog")?.close();
      latestListRequest += 1;
      applyJournalPayload(payload);
      announce(payload.message || "Catatan dihapus.");
    } catch (error) {
      announce("Catatan gagal dihapus. Coba lagi.", true);
    } finally {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      if (submitButton) submitButton.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const editLink = event.target.closest(
      ".food-journal__log-list .card__actions .btn--secondary"
    );
    if (editLink) {
      const cardItem = editLink.closest(".food-journal__card-item");
      const editTrigger = cardItem?.querySelector("[data-foodlog-edit-trigger]");
      if (!editTrigger) return;

      event.preventDefault();
      editTrigger.click();
      const form = document.querySelector("#create-log-modal .modal__form");
      if (form) {
        form.dataset.foodlogEdit = "true";
        form.dataset.foodLogId = editTrigger.dataset.logId;
      }
      return;
    }

    const deleteLink = event.target.closest(
      ".food-journal__log-list .card__actions .btn--tertiary.btn--bright"
    );
    if (deleteLink) {
      const trigger = deleteLink.closest(".food-journal__card-item")
        ?.querySelector("[data-foodlog-delete-trigger]");
      if (!trigger) return;
      event.preventDefault();
      trigger.click();
      return;
    }

    const resetLink = event.target.closest("[data-foodlog-reset]");
    if (resetLink) {
      event.preventDefault();
      const form = document.querySelector("[data-foodlog-filter-form]");
      if (form) form.reset();
      const url = new URL(resetLink.href, window.location.href);
      window.history.pushState({}, "", url);
      refreshJournal(url.href, true);
      return;
    }

    const opener = event.target.closest('[data-modal-open="#create-log-modal"]');
    if (opener && !opener.hasAttribute("data-foodlog-edit-trigger")) {
      const form = document.querySelector("#create-log-modal .modal__form");
      if (form) {
        delete form.dataset.foodlogEdit;
        delete form.dataset.foodLogId;
      }
    }
  });

  document.addEventListener("submit", (event) => {
    const filterForm = event.target.closest("[data-foodlog-filter-form]");
    if (filterForm) {
      event.preventDefault();
      const url = filterUrl(filterForm);
      window.history.pushState({}, "", url);
      refreshJournal(url.href, true);
      return;
    }

    const foodLogForm = event.target.closest("#create-log-modal .modal__form");
    if (foodLogForm) {
      event.preventDefault();
      submitFoodLogForm(foodLogForm);
      return;
    }

    const deleteForm = event.target.closest("#food-log-delete-confirm form");
    if (deleteForm) {
      event.preventDefault();
      submitDeleteForm(deleteForm);
    }
  });

  document.addEventListener("input", (event) => {
    if (!event.target.matches("[data-foodlog-filter-form] [name='query']")) return;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      const form = event.target.form;
      const url = filterUrl(form);
      window.history.replaceState({}, "", url);
      refreshJournal(url.href);
    }, 350);
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-foodlog-filter-form] select, [data-foodlog-filter-form] input[type='date']")) return;
    const form = event.target.form;
    const url = filterUrl(form);
    window.history.replaceState({}, "", url);
    refreshJournal(url.href);
  });

  window.addEventListener("popstate", () => {
    const form = document.querySelector("[data-foodlog-filter-form]");
    if (form) {
      const params = new URL(window.location.href).searchParams;
      for (const field of form.elements) {
        if (field.name) field.value = params.get(field.name) || "";
      }
    }
    refreshJournal(window.location.href);
  });
})();