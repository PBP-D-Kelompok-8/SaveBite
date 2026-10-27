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
      form.querySelector(".food-journal__edit-error")?.remove();
    }
    return;
  }

const deleteLink = event.target.closest(
    ".food-journal__log-list .card__actions .btn--tertiary.btn--bright"
  );
  if (deleteLink) {
    const cardItem = deleteLink.closest(".food-journal__card-item");
    const deleteTrigger = cardItem?.querySelector("[data-foodlog-delete-trigger]");
    if (!deleteTrigger) return;

    event.preventDefault();
    deleteTrigger.click();
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

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("#create-log-modal .modal__form");
  if (!form?.dataset.foodlogEdit) return;

  event.preventDefault();
  const modal = form.closest("dialog");
  const submitButton = form.querySelector('[type="submit"]');
  if (submitButton) submitButton.disabled = true;

  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });
    const result = await response.json();

    if (response.ok && result.ok) {
      const fragment = document.createRange().createContextualFragment(result.card_html);
      const updatedCard = fragment.querySelector(".food-journal__card-item");
      const oldCard = [...document.querySelectorAll(".food-journal__card-item")]
        .find((item) => item.dataset.logId === form.dataset.foodLogId);
      if (oldCard && updatedCard) oldCard.replaceWith(updatedCard);

      const summaryFragment = document.createRange().createContextualFragment(result.summary_html);
      const updatedSummary = summaryFragment.querySelector(".food-journal__summary");
      const oldSummary = document.querySelector(".food-journal__summary");
      if (oldSummary && updatedSummary) oldSummary.replaceWith(updatedSummary);
      modal.close();
      delete form.dataset.foodlogEdit;
      delete form.dataset.foodLogId;
      return;
    }

    if (result.form_html) {
      modal.querySelector(".modal__content").innerHTML = result.form_html;
      modal.querySelector(".input__field")?.focus();
    }
  } catch (error) {
    let notice = form.querySelector(".food-journal__edit-error");
    if (!notice) {
      notice = document.createElement("p");
      notice.className = "food-journal__edit-error";
      notice.setAttribute("role", "alert");
      form.querySelector(".modal__content")?.prepend(notice);
    }
    notice.textContent = "Perubahan belum tersimpan. Coba lagi.";
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});
