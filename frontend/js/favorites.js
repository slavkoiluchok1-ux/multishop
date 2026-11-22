/* MultiShop — favorites.js
   Обране: список товарів, прибирання, перехід на сторінку товару.
   Використовує MS (api.js), MSAuth (auth.js). */
(function () {
  "use strict";

  const esc = window.MS.escapeHtml;
  const els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function showEmpty(visible, text) {
    els.empty.hidden = !visible;
    if (text) els.empty.textContent = text;
  }

  /* ---------- Картка товару ---------- */
  function card(item) {
    const el = document.createElement("article");
    // Товар недоступний, якщо його заблокував або видалив адміністратор
    const unavailable = item.available === false;
    const soldOut = item.status !== "active";
    el.className = "product-card" + (soldOut || unavailable ? " is-soldout" : "");

    el.innerHTML =
      '<div class="product-thumb-wrap">' +
        (item.image
          ? '<img class="product-thumb" src="' + item.image + '" alt="">'
          : '<div class="product-thumb"></div>') +
        '<span class="product-price">' + esc(item.price_card) + " MC</span>" +
        '<button class="product-fav is-on" type="button" data-act="remove" aria-label="Прибрати з обраного">♥</button>' +
      "</div>" +
      '<div class="product-body">' +
        '<div class="product-name">' + esc(item.name) + "</div>" +
        '<div class="product-meta">' +
          (unavailable
            ? '<span class="badge-soft is-blood">Недоступний</span>'
            : soldOut
              ? '<span class="badge-soft is-blood">Закінчився</span>'
              : '<span class="badge-soft is-muted">' +
                esc(item.product_type_display || "") +
                (item.category_leaf ? " · " + esc(item.category_leaf) : "") +
                "</span>") +
        "</div>" +
        '<div class="product-foot">' +
          '<span class="small text-muted-soft product-seller" title="' + esc(item.seller) + '">' +
            esc(item.seller) + "</span>" +
          '<a class="btn btn-primary btn-sm" href="/pages/product.html?id=' + item.product_id + '">Переглянути</a>' +
        "</div>" +
      "</div>";

    el.querySelector('[data-act="remove"]').addEventListener("click", () => remove(item.product_id, el));
    return el;
  }

  async function remove(productId, el) {
    const button = el.querySelector('[data-act="remove"]');
    if (button.disabled) return;
    button.disabled = true;
    try {
      await window.MS.request("/favorites", { method: "DELETE", body: { product_id: productId } });
      el.classList.add("is-removing");
      await new Promise((resolve) => window.setTimeout(
        resolve,
        window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 160
      ));
      el.remove();
      window.MS.toast("Прибрано з обраного.", "success");
      if (window.MSCounters) window.MSCounters.refresh();
      const cacheKey = window.MSSkeletons.cacheKey("/favorites");
      const cached = window.MSSkeletons.getCachedData(cacheKey);
      if (cached && Array.isArray(cached.results)) {
        cached.results = cached.results.filter((item) => String(item.product_id) !== String(productId));
        window.MSSkeletons.setCachedData(cacheKey, cached);
      }
      showEmpty(els.grid.children.length === 0, "В обраному порожньо.");
    } catch (err) {
      button.disabled = false;
      window.MS.toast(err.message || "Не вдалося прибрати товар.", "danger");
    }
  }

  function paint(results) {
    els.grid.innerHTML = "";
    results.forEach((item) => els.grid.appendChild(card(item)));
    showEmpty(results.length === 0, "В обраному порожньо.");
  }

  function renderPayload(data) {
    els.error.hidden = true;
    if (!data.authenticated) {
      els.authNotice.hidden = false;
      els.grid.innerHTML = "";
      showEmpty(false);
      return;
    }
    els.authNotice.hidden = true;
    paint(data.results || []);
  }

  async function load() {
    els.error.hidden = true;
    const cacheKey = window.MSSkeletons.cacheKey("/favorites");
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (cached !== undefined) renderPayload(cached);
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton(els.grid, "product", 6)
      : null;
    try {
      const data = await window.MS.get("/favorites");
      window.MSSkeletons.setCachedData(cacheKey, data);
      if (loading) await loading.finish(() => renderPayload(data));
      else renderPayload(data);
    } catch (err) {
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити обране.");
      if (loading) {
        await loading.finish(() => {
          showEmpty(false);
          window.MSSkeletons.renderRetryError(els.grid, message, load);
        });
      } else {
        window.MSSkeletons.renderRetryError(els.error, message, load, true);
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    els.grid = $("favoritesGrid");
    els.empty = $("favoritesEmpty");
    els.error = $("favoritesError");
    els.authNotice = $("favoritesAuth");

    window.MSAuth.ready().then(load);
  });
})();
