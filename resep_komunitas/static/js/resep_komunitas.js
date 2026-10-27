// document.addEventListener("DOMContentLoaded", () => {

    /*
     * STAR PADA HALAMAN DETAIL
     * Mempertahankan mekanisme yang sudah ada.
     */

    // const starForm = document.querySelector("[data-star-form]");

    // if (starForm) {
    //     starForm.addEventListener("submit", async (event) => {
    //         event.preventDefault();

    //         const button = starForm.querySelector("button");
    //         const countElement = document.querySelector("#star-count");
    //         const csrfInput = starForm.querySelector(
    //             'input[name="csrfmiddlewaretoken"]'
    //         );

    //         if (!button || !countElement || !csrfInput) return;

    //         button.disabled = true;

    //         try {
    //             const response = await fetch(starForm.action, {
    //                 method: "POST",
    //                 headers: {
    //                     "X-Requested-With": "XMLHttpRequest",
    //                     "X-CSRFToken": csrfInput.value,
    //                 },
    //                 body: new FormData(starForm),
    //                 credentials: "same-origin",
    //             });

    //             const data = await response.json();

    //             if (!response.ok) {
    //                 throw new Error(
    //                     data.error || "Gagal menyimpan star."
    //                 );
    //             }

    //             countElement.textContent = `☆ ${data.star_count}`;

    //             button.textContent = data.is_starred
    //                 ? "Tersimpan ★"
    //                 : "Simpan Menu ★";

    //         } catch (error) {
    //             window.alert(
    //                 error.message || "Terjadi kesalahan. Coba lagi."
    //             );
    //         } finally {
    //             button.disabled = false;
    //         }
    //     });
    // }


    /*
     * STAR PADA CARD RESEP
     */

    // const cardStarForms = document.querySelectorAll(
    //     "[data-star-card-form]"
    // );

    // cardStarForms.forEach((form) => {

    //     form.addEventListener("submit", async (event) => {
    //         event.preventDefault();

    //         const button = form.querySelector("button");
    //         const icon = form.querySelector("[data-star-icon]");
    //         const csrfInput = form.querySelector(
    //             'input[name="csrfmiddlewaretoken"]'
    //         );

    //         const card = form.closest(".recipe-card");
    //         const countElement = card?.querySelector("[data-star-count]");

    //         if (!button || !icon || !csrfInput || !countElement) {
    //             return;
    //         }

    //         button.disabled = true;

    //         try {
    //             const response = await fetch(form.action, {
    //                 method: "POST",
    //                 headers: {
    //                     "X-Requested-With": "XMLHttpRequest",
    //                     "X-CSRFToken": csrfInput.value,
    //                 },
    //                 body: new FormData(form),
    //                 credentials: "same-origin",
    //             });

    //             const data = await response.json();

    //             if (!response.ok) {
    //                 throw new Error(
    //                     data.error || "Gagal memperbarui star."
    //                 );
    //             }

                /*
                 * Perbarui jumlah star.
                 */
                //countElement.textContent = data.star_count;

                /*
                 * Gunakan URL static yang disiapkan Django
                 * melalui data attribute pada tombol.
                 */
//                 const staticPath = data.is_starred
//                     ? button.dataset.starFilled
//                     : button.dataset.starOutline;

//                 if (staticPath) {
//                     icon.style.setProperty(
//                         "--icon",
//                         `url("${staticPath}")`
//                     );
//                 }

//                 button.setAttribute(
//                     "aria-pressed",
//                     String(data.is_starred)
//                 );

//                 button.setAttribute(
//                     "aria-label",
//                     data.is_starred
//                         ? "Batalkan star"
//                         : "Beri star"
//                 );

//             } catch (error) {
//                 window.alert(
//                     error.message || "Terjadi kesalahan. Coba lagi."
//                 );
//             } finally {
//                 button.disabled = false;
//             }
//         });

//     });

// });

// Delete Modal
document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("delete-modal");
    const deleteForm = document.getElementById("delete-recipe-form");
    const recipeTitle = document.getElementById("delete-recipe-title");

    if (!modal || !deleteForm || !recipeTitle) {
        console.error("Elemen delete modal tidak ditemukan.");
        return;
    }

    const closeButtons = modal.querySelectorAll("[data-close-delete-modal]");
    let lastFocusedElement = null;

    function openModal(button) {
        lastFocusedElement = button;

        deleteForm.action = button.dataset.deleteUrl;
        recipeTitle.textContent = button.dataset.recipeTitle;

        modal.classList.add("is-open");
        modal.setAttribute("aria-hidden", "false");
        document.body.classList.add("delete-modal-open");

        const cancelButton = modal.querySelector(
            'button[data-close-delete-modal]'
        );

        cancelButton?.focus();
    }

    function closeModal() {
        modal.classList.remove("is-open");
        modal.setAttribute("aria-hidden", "true");
        document.body.classList.remove("delete-modal-open");

        lastFocusedElement?.focus();
    }

    // Event delegation: menangani klik tombol hapus pada semua recipe card.
    document.addEventListener("click", (event) => {
        const deleteButton = event.target.closest("[data-delete-modal]");

        if (!deleteButton) {
            return;
        }

        event.preventDefault();
        openModal(deleteButton);
    });

    // Menutup modal melalui tombol Batal atau overlay.
    closeButtons.forEach((button) => {
        button.addEventListener("click", closeModal);
    });

    // Menutup modal menggunakan tombol Escape.
    document.addEventListener("keydown", (event) => {
        if (
            event.key === "Escape" &&
            modal.classList.contains("is-open")
        ) {
            closeModal();
        }
    });
});