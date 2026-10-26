
document.addEventListener("DOMContentLoaded", () => {
    const starForm = document.querySelector("[data-star-form]");

    if (!starForm) return;

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
                throw new Error(data.error || "Gagal menyimpan star.");
            }

            countElement.textContent = `☆ ${data.star_count}`;

            button.textContent = data.is_starred
                ? "Tersimpan ★"
                : "Simpan Menu ★";
        } catch (error) {
            window.alert(error.message || "Terjadi kesalahan. Coba lagi.");
        } finally {
            button.disabled = false;
        }
    });
});
