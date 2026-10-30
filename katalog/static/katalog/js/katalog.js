// Fetch API dipakai untuk memperbarui hasil pencarian tanpa memuat ulang halaman.
const searchForm = document.getElementById("kat-search-form");
const results = document.getElementById("kat-results");
const resultStatus = document.getElementById("kat-result-status");

if (searchForm && results && resultStatus) {
  searchForm.addEventListener("submit", function (event) {
    event.preventDefault();

    const query = document.getElementById("kat-query").value.trim();
    const ingredients = document.getElementById("kat-ingredients").value.trim();

    if (!query && !ingredients) {
      results.innerHTML = "";
      resultStatus.textContent = "Masukkan nama resep atau bahan sisa untuk memulai.";
      return;
    }

    const params = new URLSearchParams(new FormData(searchForm));
    resultStatus.textContent = "Sedang mencari resep...";

    fetch(searchForm.dataset.searchUrl + "?" + params.toString())
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Pencarian gagal. Coba lagi.");
        }
        return response.text();
      })
      .then(function (html) {
        results.innerHTML = html;

        const recipeCount = results.querySelectorAll(".kat-recipe-card").length;
        if (recipeCount > 0) {
          resultStatus.textContent = recipeCount + " resep ditemukan.";
        } else if (results.querySelector(".kat-error-state")) {
          resultStatus.textContent = "Pencarian belum selesai.";
        } else if (results.querySelector(".kat-empty-state")) {
          resultStatus.textContent = "0 resep ditemukan.";
        } else {
          resultStatus.textContent = "Pencarian selesai.";
        }
      })
      .catch(function () {
        resultStatus.textContent = "Pencarian gagal. Periksa koneksi internet lalu coba lagi.";
      });
  });
}
