(function () {
  "use strict";

  const app = document.getElementById("katalog-app");
  const searchForm = document.getElementById("kat-search-form");
  const resultsContainer = document.getElementById("kat-results");
  const statusText = document.getElementById("kat-status");
  const resultCount = document.getElementById("kat-result-count");
  const toast = document.getElementById("kat-toast");
  let toastTimer = null;
  let searchNumber = 0;

  function readJsonResponse(response) {
    return response.json().catch(function () {
      throw new Error("Server mengirim respons yang tidak valid. Muat ulang halaman lalu coba lagi.");
    });
  }

  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.hidden = true;
    }, 4500);
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function getCsrfToken(form) {
    let input = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
    if (!input) {
      input = document.querySelector('#kat-csrf-form input[name="csrfmiddlewaretoken"]');
    }
    return input ? input.value : "";
  }

  function formBody(form) {
    const body = new URLSearchParams();
    const formData = new FormData(form);
    formData.forEach(function (value, key) {
      if (typeof value === "string") body.append(key, value);
    });
    return body.toString();
  }

  function postHeaders(token) {
    return {
      "Accept": "application/json",
      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      "X-CSRFToken": token
    };
  }

  function detailUrl(mealId) {
    if (!app) return "";
    const template = app.dataset.detailUrlTemplate;
    if (!template) return "";
    return template.replace(/\/0\/$/, "/" + encodeURIComponent(String(mealId)) + "/");
  }

  function makeRecipeCard(meal) {
    const article = makeElement("article", "kat-recipe-card");
    const imageUrl = typeof meal.strMealThumb === "string" ? meal.strMealThumb : "";

    if (imageUrl) {
      try {
        const parsedUrl = new URL(imageUrl);
        if (parsedUrl.protocol === "https:" && parsedUrl.hostname === "www.themealdb.com") {
          const image = makeElement("img", "kat-card-image");
          image.src = parsedUrl.href;
          image.alt = "Foto " + (meal.strMeal || "resep");
          image.loading = "lazy";
          image.referrerPolicy = "no-referrer";
          article.appendChild(image);
        }
      } catch (_error) {
        // Jika URL gambar tidak valid, kartu resep tetap bisa ditampilkan tanpa gambar.
      }
    }

    const body = makeElement("div", "kat-card-body");
    body.appendChild(makeElement("h2", "", meal.strMeal || "Resep tanpa nama"));

    const metaParts = [meal.strCategory, meal.strArea].filter(function (value) {
      return typeof value === "string" && value.length > 0;
    });
    if (Number.isInteger(meal.matches) && meal.matches > 0) {
      metaParts.push(meal.matches + " bahan cocok");
    }
    if (metaParts.length) body.appendChild(makeElement("p", "kat-card-meta", metaParts.join(" · ")));

    const actions = makeElement("div", "kat-card-actions");
    const link = makeElement("a", "kat-button kat-button--small", "Lihat resep");
    link.href = detailUrl(meal.idMeal);
    actions.appendChild(link);

    if (app.dataset.isAuthenticated === "true") {
      const saveButton = makeElement("button", "kat-button kat-button--outline kat-button--small", "Simpan resep");
      saveButton.type = "button";
      saveButton.dataset.saveMealId = String(meal.idMeal);
      actions.appendChild(saveButton);
    } else {
      const loginLink = makeElement("a", "kat-button kat-button--outline kat-button--small", "Login untuk simpan");
      loginLink.href = app.dataset.loginUrl || "/login/";
      actions.appendChild(loginLink);
    }

    body.appendChild(actions);
    article.appendChild(body);
    return article;
  }

  function renderMeals(meals) {
    resultsContainer.replaceChildren();
    if (!meals.length) {
      resultCount.textContent = "0 resep ditemukan.";
      statusText.textContent = "Belum ada resep yang cocok. Coba kata lain atau kurangi jumlah bahan.";
      return;
    }

    const fragment = document.createDocumentFragment();
    meals.forEach(function (meal) {
      fragment.appendChild(makeRecipeCard(meal));
    });
    resultsContainer.appendChild(fragment);
    resultCount.textContent = meals.length + " resep ditampilkan.";
    statusText.textContent = "Pilih resep untuk melihat bahan dan cara memasaknya.";
  }

  async function searchRecipes(event) {
    if (event) event.preventDefault();
    if (!app || !searchForm || !resultsContainer || !resultCount || !statusText) return;

    const queryInput = document.getElementById("kat-query");
    const ingredientsInput = document.getElementById("kat-ingredients");
    if (!queryInput || !ingredientsInput) return;

    const params = new URLSearchParams();
    const query = queryInput.value.trim();
    const ingredients = ingredientsInput.value.trim();
    const thisSearch = ++searchNumber;

    if (!query && !ingredients) {
      resultsContainer.replaceChildren();
      resultCount.textContent = "Masukkan nama resep atau bahan sisa untuk memulai.";
      statusText.textContent = "";
      return;
    }

    const ingredientParts = ingredients.split(",").map(function (part) {
      return part.trim().toLowerCase();
    }).filter(Boolean);
    const uniqueIngredientParts = Array.from(new Set(ingredientParts));
    if (uniqueIngredientParts.length > 3) {
      resultsContainer.replaceChildren();
      resultCount.textContent = "";
      statusText.textContent = "Masukkan maksimal 3 bahan sekaligus.";
      return;
    }

    if (query) params.set("q", query);
    if (ingredients) params.set("ingredients", ingredients);

    resultsContainer.replaceChildren();
    resultCount.textContent = "";
    statusText.textContent = "Sedang mencari resep…";

    try {
      const response = await fetch(app.dataset.searchUrl + "?" + params.toString(), {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin"
      });
      const data = await readJsonResponse(response);
      if (!response.ok) throw new Error(data.error || "Pencarian gagal. Coba lagi.");
      if (thisSearch !== searchNumber) return;
      if (!data || !Array.isArray(data.results)) throw new Error("Format hasil pencarian tidak valid.");
      renderMeals(data.results);
    } catch (error) {
      if (thisSearch !== searchNumber) return;
      resultCount.textContent = "";
      statusText.textContent = error.message || "Koneksi gagal. Periksa internet lalu coba lagi.";
    }
  }

  async function saveRecipe(button) {
    const mealId = button.dataset.saveMealId;
    if (!app || !mealId) return;

    const token = getCsrfToken(null);
    if (!token) {
      showToast("Token keamanan tidak ditemukan. Muat ulang halaman lalu coba lagi.");
      return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Menyimpan…";

    try {
      const body = new URLSearchParams({ meal_id: mealId });
      const response = await fetch(app.dataset.saveUrl, {
        method: "POST",
        headers: postHeaders(token),
        credentials: "same-origin",
        body: body.toString()
      });
      const data = await readJsonResponse(response);
      if (response.status === 401) {
        showToast(data.error || "Silakan login untuk menyimpan resep.");
        button.disabled = false;
        button.textContent = originalText;
        return;
      }
      if (!response.ok) throw new Error(data.error || "Resep tidak berhasil disimpan.");
      button.textContent = "Tersimpan";
      button.disabled = true;
      showToast(data.message || "Resep berhasil disimpan.");
    } catch (error) {
      showToast(error.message || "Koneksi gagal. Coba lagi.");
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  async function updateSavedNote(form) {
    const status = document.getElementById("kat-edit-status");
    const button = form.querySelector('button[type="submit"]');
    const token = getCsrfToken(form);
    if (!token) {
      if (status) status.textContent = "Token keamanan tidak ditemukan. Muat ulang halaman lalu coba lagi.";
      return;
    }

    if (button) {
      button.disabled = true;
      button.textContent = "Menyimpan…";
    }
    if (status) status.textContent = "Menyimpan catatan…";

    try {
      const response = await fetch(form.action || window.location.href, {
        method: "POST",
        headers: postHeaders(token),
        credentials: "same-origin",
        body: formBody(form)
      });
      const data = await readJsonResponse(response);
      if (!response.ok) throw new Error(data.error || "Catatan tidak berhasil disimpan.");
      if (status) status.textContent = data.message || "Catatan resep berhasil diperbarui.";
      showToast(data.message || "Catatan resep berhasil diperbarui.");
    } catch (error) {
      if (status) status.textContent = error.message || "Koneksi gagal. Coba lagi.";
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Simpan perubahan";
      }
    }
  }

  async function deleteSavedRecipe(form) {
    const token = getCsrfToken(form);
    const button = form.querySelector('button[type="submit"]');
    if (!token) {
      showToast("Token keamanan tidak ditemukan. Muat ulang halaman lalu coba lagi.");
      return;
    }
    if (!window.confirm("Hapus resep ini dari daftar tersimpan?")) return;

    if (button) {
      button.disabled = true;
      button.textContent = "Menghapus…";
    }

    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: postHeaders(token),
        credentials: "same-origin",
        body: formBody(form)
      });
      const data = await readJsonResponse(response);
      if (!response.ok) throw new Error(data.error || "Resep tidak berhasil dihapus.");

      const card = form.closest("[data-saved-card]");
      if (card) card.remove();

      const grid = document.getElementById("kat-saved-grid");
      const remaining = grid ? grid.querySelectorAll("[data-saved-card]").length : 0;
      const count = document.getElementById("kat-saved-count");
      const status = document.getElementById("kat-saved-status");
      const emptyState = document.getElementById("kat-saved-empty");
      if (count) count.textContent = remaining + " resep tersimpan";
      if (status) status.textContent = data.message || "Resep dihapus dari daftar tersimpan.";
      if (remaining === 0) {
        if (grid) grid.hidden = true;
        if (emptyState) emptyState.hidden = false;
      }
      showToast(data.message || "Resep dihapus dari daftar tersimpan.");
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = "Hapus dari daftar";
      }
      showToast(error.message || "Koneksi gagal. Coba lagi.");
    }
  }

  if (searchForm) {
    searchForm.addEventListener("submit", searchRecipes);

    // Jalankan pencarian otomatis bila halaman dibuka dari tautan Planner dengan bahan terisi.
    const initialQuery = document.getElementById("kat-query");
    const initialIngredients = document.getElementById("kat-ingredients");
    if ((initialQuery && initialQuery.value.trim()) ||
        (initialIngredients && initialIngredients.value.trim())) {
      searchRecipes();
    }
  }

  const resetButton = document.getElementById("kat-reset");
  if (resetButton) {
    resetButton.addEventListener("click", function () {
      // Batalkan efek request lama, termasuk saat halaman awalnya dibuka dari tautan Planner.
      searchNumber += 1;
      window.setTimeout(function () {
        const queryInput = document.getElementById("kat-query");
        const ingredientsInput = document.getElementById("kat-ingredients");
        if (queryInput) queryInput.value = "";
        if (ingredientsInput) ingredientsInput.value = "";
        if (resultsContainer) resultsContainer.replaceChildren();
        if (resultCount) resultCount.textContent = "Masukkan nama resep atau bahan sisa untuk memulai.";
        if (statusText) statusText.textContent = "";
      }, 0);
    });
  }

  document.addEventListener("click", function (event) {
    if (!event.target || typeof event.target.closest !== "function") return;
    const button = event.target.closest("[data-save-meal-id]");
    if (button) saveRecipe(button);
  });

  document.addEventListener("submit", function (event) {
    if (!event.target || typeof event.target.matches !== "function") return;
    if (event.target.matches("#kat-edit-form")) {
      event.preventDefault();
      updateSavedNote(event.target);
    } else if (event.target.matches(".kat-delete-form")) {
      event.preventDefault();
      deleteSavedRecipe(event.target);
    }
  });
})();
