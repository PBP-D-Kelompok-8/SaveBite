document.addEventListener("DOMContentLoaded", () => {

    /*
     * STAR PADA HALAMAN DETAIL
     * Mempertahankan mekanisme yang sudah ada.
     */

    const starForm = document.querySelector("[data-star-form]");

    if (starForm) {
        starForm.addEventListener("submit", async (event) => {
            event.preventDefault();

            const button = starForm.querySelector("button");
            const countElement = document.querySelector("#star-count");
            const csrfInput = starForm.querySelector(
                'input[name="csrfmiddlewaretoken"]'
            );

            if (!button || !countElement || !csrfInput) return;

            button.disabled = true;

            try {
                const response = await fetch(starForm.action, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": csrfInput.value,
                    },
                    body: new FormData(starForm),
                    credentials: "same-origin",
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.error || "Gagal menyimpan star."
                    );
                }

                countElement.textContent = `☆ ${data.star_count}`;

                button.textContent = data.is_starred
                    ? "Tersimpan ★"
                    : "Simpan Menu ★";

            } catch (error) {
                window.alert(
                    error.message || "Terjadi kesalahan. Coba lagi."
                );
            } finally {
                button.disabled = false;
            }
        });
    }


    /*
     * STAR PADA CARD RESEP
     */

    const cardStarForms = document.querySelectorAll(
        "[data-star-card-form]"
    );

    cardStarForms.forEach((form) => {

        form.addEventListener("submit", async (event) => {
            event.preventDefault();

            const button = form.querySelector("button");
            const icon = form.querySelector("[data-star-icon]");
            const csrfInput = form.querySelector(
                'input[name="csrfmiddlewaretoken"]'
            );

            const card = form.closest(".recipe-card");
            const countElement = card?.querySelector("[data-star-count]");

            if (!button || !icon || !csrfInput || !countElement) {
                return;
            }

            button.disabled = true;

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": csrfInput.value,
                    },
                    body: new FormData(form),
                    credentials: "same-origin",
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(
                        data.error || "Gagal memperbarui star."
                    );
                }

                /*
                 * Perbarui jumlah star.
                 */
                countElement.textContent = data.star_count;

                /*
                 * Perbarui ikon dan status tombol.
                 */
                const iconPath = data.is_starred
                    ? "icons/star-filled.svg"
                    : "icons/star-outline.svg";

                /*
                 * Gunakan URL static yang disiapkan Django
                 * melalui data attribute pada tombol.
                 */
                const staticPath = data.is_starred
                    ? button.dataset.starFilled
                    : button.dataset.starOutline;

                if (staticPath) {
                    icon.style.setProperty(
                        "--icon",
                        `url("${staticPath}")`
                    );
                }

                button.setAttribute(
                    "aria-pressed",
                    String(data.is_starred)
                );

                button.setAttribute(
                    "aria-label",
                    data.is_starred
                        ? "Batalkan star"
                        : "Beri star"
                );

            } catch (error) {
                window.alert(
                    error.message || "Terjadi kesalahan. Coba lagi."
                );
            } finally {
                button.disabled = false;
            }
        });

    });

});