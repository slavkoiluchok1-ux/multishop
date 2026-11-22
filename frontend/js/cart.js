/* MultiShop — cart.js
   Кошик: склад, зміна кількості, прибирання, червона позначка про зміну ціни
   та сірий недоступний товар. Використовує MS (api.js), MSAuth (auth.js). */
(function () {
  "use strict";

  const esc = window.MS.escapeHtml;
  const els = {};
  let busy = false;

  function $(id) {
    return document.getElementById(id);
  }

  function setEmpty(visible, text) {
    els.empty.hidden = !visible;
    if (text) els.empty.textContent = text;
  }

  /* ---------- Один рядок кошика ---------- */
  function renderItem(item) {
    const row = document.createElement("article");
    row.className = "card-soft cart-row" + (item.available ? "" : " is-unavailable");
    row.dataset.variant = String(item.variant_id);

    const notes = [];
    if (!item.available) {
      notes.push('<span class="badge-soft is-muted">Товар закінчився</span>');
    }
    if (item.price_changed) {
      notes.push(
        '<span class="cart-price-changed">Ціна товару змінилась: було ' +
          esc(item.price_at_add) + " MC</span>"
      );
    }

    row.innerHTML =
      (item.image
        ? '<img class="cart-thumb" src="' + item.image + '" alt="">'
        : '<div class="cart-thumb"></div>') +
      '<div class="cart-info">' +
        '<div class="product-name">' + esc(item.name) + "</div>" +
        '<div class="small text-muted-soft">' + esc(item.sku) + " · " + esc(item.seller) + "</div>" +
        notes.join(" ") +
      "</div>" +
      '<div class="cart-controls">' +
        '<div class="qty-group">' +
          '<button class="qty-btn" type="button" data-act="dec" aria-label="Менше">−</button>' +
          '<input class="qty-input" type="number" min="1" max="' + item.stock + '" value="' +
            item.quantity + '" data-act="qty"' + (item.available ? "" : " disabled") + ">" +
          '<button class="qty-btn" type="button" data-act="inc" aria-label="Більше">+</button>' +
        "</div>" +
        '<span class="cart-subtotal">' + esc(item.subtotal_full) + " MC</span>" +
        '<button class="btn btn-outline-primary btn-sm" type="button" data-act="remove">Прибрати</button>' +
      "</div>";

    row.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-act]");
      if (!btn || btn.getAttribute("data-act") === "qty") return;
      const act = btn.getAttribute("data-act");
      if (act === "remove") return changeQuantity(item.variant_id, 0);
      if (!item.available) return;
      if (act === "inc") return changeQuantity(item.variant_id, item.quantity + 1);
      if (act === "dec") return changeQuantity(item.variant_id, item.quantity - 1);
    });

    row.querySelector('[data-act="qty"]').addEventListener("change", (event) => {
      const value = parseInt(event.target.value || "1", 10);
      changeQuantity(item.variant_id, value > 0 ? value : 1);
    });

    return row;
  }

  /* ---------- Малювання всього кошика ---------- */
  function paint(payload) {
    const items = (payload && payload.items) || [];
    els.list.innerHTML = "";
    items.forEach((item) => els.list.appendChild(renderItem(item)));
    els.total.textContent = (payload.total_full || "0") + " MC";
    els.count.textContent = String((payload && payload.count) || 0);
    els.summary.hidden = items.length === 0;
    setEmpty(items.length === 0, "Кошик порожній. Додайте товари з каталогу.");
    window.MSSkeletons.setCachedData(window.MSSkeletons.cacheKey("/cart"), payload);
  }

  async function changeQuantity(variantId, quantity) {
    if (busy) return;
    busy = true;
    const row = Array.from(els.list.querySelectorAll(".cart-row")).find(
      (item) => item.dataset.variant === String(variantId)
    );
    try {
      const payload = await window.MS.post("/cart/items", {
        variant_id: variantId,
        quantity: quantity,
      });
      if (quantity === 0 && row) {
        row.classList.add("is-removing");
        await new Promise((resolve) => window.setTimeout(
          resolve,
          window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 160
        ));
      }
      paint(payload);
      if (window.MSCounters) window.MSCounters.refresh();
    } catch (err) {
      window.MS.toast(err.message || "Не вдалося оновити кошик.", "danger");
    } finally {
      busy = false;
    }
  }

  async function load() {
    const cacheKey = window.MSSkeletons.cacheKey("/cart");
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (cached !== undefined) paint(cached);
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton(els.list, "cart", 3)
      : null;
    try {
      const payload = await window.MS.get("/cart");
      window.MSSkeletons.setCachedData(cacheKey, payload);
      if (loading) await loading.finish(() => paint(payload));
      else paint(payload);
    } catch (err) {
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити кошик.");
      if (loading) {
        await loading.finish(() => {
          els.summary.hidden = true;
          setEmpty(false);
          window.MSSkeletons.renderRetryError(els.list, message, load);
        });
      } else {
        window.MSSkeletons.renderRetryError(els.list, message, load, true);
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    els.list = $("cartItems");
    els.empty = $("cartEmpty");
    els.summary = $("cartSummary");
    els.total = $("cartTotal");
    els.count = $("cartCount");

    window.MSAuth.ready().then(load);
  });
})();
