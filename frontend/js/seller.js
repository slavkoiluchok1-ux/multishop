/* MultiShop — seller verification and listing management. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = window.MS.escapeHtml;
  // Ціни показуємо однаково з рештою сторінок: до 3 знаків після коми.
  const fmt = (value) => (window.MSPrice ? window.MSPrice.formatMCShort(value) : value);
  const PRICE_SCALE = 1000000;
  const MAX_PRICE_MICROS = 999999999 * PRICE_SCALE;

  function priceToMicros(value) {
    const match = /^(\d+)(?:[.,](\d{0,6}))?$/.exec(String(value).trim());
    if (!match) return null;
    const fraction = (match[2] || "").padEnd(6, "0");
    const micros = Number(match[1]) * PRICE_SCALE + Number(fraction || "0");
    return Number.isSafeInteger(micros) && micros > 0 && micros <= MAX_PRICE_MICROS
      ? micros
      : null;
  }

  function microsToPrice(value) {
    const whole = Math.floor(value / PRICE_SCALE);
    const fraction = String(value % PRICE_SCALE).padStart(6, "0").replace(/0+$/, "");
    return fraction ? whole + "," + fraction : String(whole);
  }

  function priceInputValue(value) {
    return String(value).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ",");
  }

  function safeWebUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch (_err) {
      return "";
    }
  }
  const categoryState = {
    view: "physical",
    path: [],
    pending: null,
    selected: null,
    rootTypes: [],
    attributes: [],
    productType: "",
  };
  let imagePreviewUrl = "";
  let activationCheckTimer = 0;
  let activationCheckVersion = 0;
  let sellerInitialized = false;
  let productsAbortController = null;
  let sellerSearchTimer = 0;
  let lastProductsHash = "";
  const debugStats = {
    init: 0,
    productsFetch: 0,
    productsRender: 0,
    verificationFetch: 0,
    walletFetch: 0,
  };
  const VERIFICATION_CACHE_PREFIX = "ms_seller_verified:";

  function verificationCacheKey(userId) {
    return VERIFICATION_CACHE_PREFIX + String(userId);
  }

  function readVerificationCache(userId) {
    try {
      const cached = JSON.parse(window.sessionStorage.getItem(verificationCacheKey(userId)) || "null");
      return cached && Number.isInteger(cached.age) && cached.age >= 18
        ? cached
        : null;
    } catch (_err) {
      return null;
    }
  }

  function writeVerificationCache(userId, age) {
    try {
      window.sessionStorage.setItem(
        verificationCacheKey(userId),
        JSON.stringify({ verified: true, age: Number(age) })
      );
    } catch (_err) {
      // Verification state still works in memory if sessionStorage is unavailable.
    }
  }

  function clearOtherVerificationCaches(userId) {
    try {
      for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
        const key = window.sessionStorage.key(i);
        if (key && key.startsWith(VERIFICATION_CACHE_PREFIX) &&
            key !== verificationCacheKey(userId)) {
          window.sessionStorage.removeItem(key);
        }
      }
    } catch (_err) {
      // Storage may be disabled.
    }
  }

  function showVerified(age) {
    const status = $("verificationStatus");
    const form = $("verificationForm");
    $("verificationInstructions").hidden = true;
    $("verificationPanel").classList.remove("is-loading");
    status.innerHTML =
      '<span class="badge-soft is-lime verification-badge">✓ Верифіковано, ' +
      esc(age) + ' років</span><p class="text-muted-soft mb-0 mt-2">Дані верифікації незмінні</p>';
    form.hidden = true;
    $("productListPanel").hidden = false;
  }

  function showVerificationError() {
    $("verificationInstructions").hidden = true;
    $("verificationPanel").classList.remove("is-loading");
    $("verificationStatus").innerHTML =
      '<div class="verification-error">Не вдалося завантажити статус' +
      ' <button class="btn btn-outline-primary btn-sm" type="button" id="retryVerification">Спробувати ще раз</button></div>';
    $("verificationForm").hidden = true;
    $("productListPanel").hidden = true;
    $("retryVerification").addEventListener("click", async () => {
      showVerificationLoading();
      try {
        const data = await window.MSAuth.refresh({ strict: true });
        await loadVerificationState(true, data);
      } catch (_err) {
        showVerificationError();
      }
    });
  }

  function showVerificationLoading() {
    $("verificationInstructions").hidden = true;
    $("verificationForm").hidden = true;
    $("verificationPanel").classList.add("is-loading");
    $("verificationStatus").innerHTML =
      '<span class="verification-neutral verification-skeleton" aria-label="Завантаження статусу верифікації"></span>';
  }

  function error(message) {
    $("sellerError").textContent = message || "";
    $("sellerError").hidden = !message;
    if (message) $("sellerSuccess").hidden = true;
  }

  function success(message) {
    $("sellerSuccess").textContent = message || "";
    $("sellerSuccess").hidden = !message;
    if (message) $("sellerError").hidden = true;
  }

  function viewForType(type) {
    if (type === "digital_file" || type === "digital_key") return "digital";
    if (type === "service" || type === "subscription") return "services";
    return "physical";
  }

  function typesForView(view) {
    if (view === "digital") return ["digital_file", "digital_key"];
    if (view === "services") return ["service", "subscription"];
    return ["physical"];
  }

  function cleanSellerAttributes(attributes) {
    const seenIds = new Set();
    const supportedKinds = new Set(["single", "multiple", "number", "bool"]);
    const clean = [];
    (Array.isArray(attributes) ? attributes : []).forEach((attribute) => {
      if (!attribute || typeof attribute !== "object") return;
      const id = Number(attribute.id);
      const name = typeof attribute.name === "string" ? attribute.name.trim() : "";
      const kind = attribute.kind;
      if (!Number.isInteger(id) || id < 1 || !name || !supportedKinds.has(kind)) return;
      if (name.toLocaleLowerCase() === "бренд" || seenIds.has(id)) return;

      let options = [];
      if (kind === "single" || kind === "multiple") {
        const values = new Set();
        options = (Array.isArray(attribute.options) ? attribute.options : [])
          .map((option) => option && typeof option.value === "string" ? option.value.trim() : "")
          .filter((value) => {
            if (!value || values.has(value)) return false;
            values.add(value);
            return true;
          })
          .map((value) => ({ value: value }));
        if (!options.length) return;
      }
      seenIds.add(id);
      clean.push({
        id: id,
        name: name,
        kind: kind,
        unit: typeof attribute.unit === "string" ? attribute.unit : "",
        required: attribute.required === true,
        options: options,
      });
    });
    return clean;
  }

  function productTypeLabel(type) {
    return {
      physical: "Фізичний товар",
      digital_file: "Цифровий файл",
      digital_key: "Ключ або ліцензія",
      service: "Послуга",
      subscription: "Підписка",
    }[type] || type;
  }

  function isOneTimeDevelopmentPath(path) {
    return path === "Послуги › Розробка" || path.startsWith("Послуги › Розробка › ");
  }

  function usesDigitalDelivery(product) {
    return product.product_type === "digital_file" ||
      (product.product_type === "service" && isOneTimeDevelopmentPath(product.category_path || ""));
  }

  // Тип беремо з обраної категорії: digital_file або готова розробка потребують файлів
  function requiresDigitalFiles() {
    if (categoryState.productType === "digital_file") return true;
    return categoryState.productType === "service" &&
      !!categoryState.selected && isOneTimeDevelopmentPath(categoryState.selected.path);
  }

  function updateTypeDisplay() {
    const label = $("productType");
    if (!label) return;
    label.textContent = categoryState.productType
      ? productTypeLabel(categoryState.productType)
      : "Оберіть категорію";
  }

  function formatFileSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " МБ";
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + " КБ";
    return bytes + " Б";
  }

  function renderProduct(product) {
    const moderated = !!product.moderation && product.moderation !== "ok";
    const moderationBadge = product.moderation === "blocked"
      ? '<span class="badge-soft is-blood">Заблоковано адміністратором</span>'
      : product.moderation === "deleted"
        ? '<span class="badge-soft is-blood">Видалено адміністратором</span>'
        : "";
    // Причина блокування/видалення — щоб продавець знав, що виправити
    const moderationReason = moderated && product.moderation_reason
      ? '<p class="seller-moderation-reason text-danger small mb-1">Причина: ' +
        esc(product.moderation_reason) + "</p>"
      : "";
    const variants = (product.variants || []).map((variant) => {
      const restockForm = product.can_restock && !moderated
        ? '<form class="seller-restock-form" data-restock-form data-product-id="' +
          product.id + '" data-variant-id="' + variant.id + '">' +
          '<label class="seller-restock-quantity">Додати кількість<input class="form-control form-control-sm" ' +
          'type="number" min="1" max="2147483647" step="1" value="1" required data-restock-quantity></label>' +
          (variant.requires_activation_codes
            ? '<label class="seller-restock-codes"><span>Коди для нових одиниць (по одному на рядок)</span>' +
              '<textarea class="form-control form-control-sm" rows="2" required data-restock-codes></textarea></label>'
            : "") +
          '<button class="btn btn-outline-primary btn-sm" type="submit">Поповнити</button>' +
          '<span class="field-hint">Заставу за додану кількість буде заблоковано з балансу.</span></form>'
        : "";
      const priceForm = !moderated && variant.can_change_price
        ? '<form class="seller-price-form" data-price-form data-product-id="' +
          product.id + '" data-variant-id="' + variant.id + '">' +
          '<label class="seller-price-input">Нова ціна, MC<span class="seller-price-control">' +
          '<input class="form-control form-control-sm" ' +
          'type="text" inputmode="decimal" maxlength="16" value="' +
          esc(priceInputValue(variant.price_mc)) + '" required data-price-value data-original-price="' +
          esc(priceInputValue(variant.price_mc)) + '">' +
          '<span class="seller-price-steppers">' +
          '<button class="btn btn-outline-secondary btn-sm" type="button" data-price-step="1" ' +
          'aria-label="Збільшити ціну на 0,000001 MC" title="+0,000001 MC">▲</button>' +
          '<button class="btn btn-outline-secondary btn-sm" type="button" data-price-step="-1" ' +
          'aria-label="Зменшити ціну на 0,000001 MC" title="−0,000001 MC">▼</button></span></span></label>' +
          '<button class="btn btn-outline-primary btn-sm" type="submit">Змінити</button>' +
          '<span class="field-hint">Вкажіть повну нову ціну. Застава перераховується за ціною та залишком; надлишок заморожується на 14 днів.</span></form>'
        : (!moderated && !variant.can_change_price
          ? '<span class="field-hint seller-price-unavailable">Ціну проданого варіанта змінити не можна.</span>'
          : "");
      return '<li class="seller-variant-summary"><span>' + esc(variant.sku || "Без артикула") +
        '</span><strong>' + esc(fmt(variant.price_mc)) + " MC</strong><span>Залишок: " +
        esc(variant.stock) + "</span>" + priceForm + restockForm + "</li>";
    }).join("");
    const digitalFiles = product.digital_files || [];
    const files = digitalFiles.map((file) =>
      '<div class="seller-file-row"><span class="seller-file-title" title="' +
      esc(file.title) + '">' + esc(file.title) +
      (file.is_trial ? " · пробний" : " · платний") + "</span>" +
      (moderated ? "" :
        '<button class="btn btn-link btn-sm text-danger" data-remove-file="' +
        file.id + '" data-product-id="' + product.id + '">Видалити</button>') +
      "</div>"
    ).join("");
    const nextStatus = product.status === "active" ? "inactive" : "active";
    const statusAction = product.status === "hidden" ? "Зупинити" :
      (product.status === "active" ? "Призупинити продаж" : "Відновити продаж");
    // Підказка про долю застави при зупинці продажу
    const statusHint = product.status === "active"
      ? ' title="Застава повернеться на ваш баланс через 14 днів"'
      : "";
    // Заблокований чи видалений товар продавець змінювати не може
    const statusButton = moderated ? "" :
      '<button class="btn btn-outline-primary btn-sm seller-status-button" data-toggle-status="' +
      product.id + '" data-status="' + nextStatus + '"' + statusHint + ">" + statusAction + "</button>";
    const hiddenUrl = product.hidden_url
      ? '<div class="seller-hidden-link"><span>Приватне посилання</span><a href="' +
        esc(product.hidden_url) + '">' + esc(product.hidden_url) + "</a></div>"
      : "";
    const portfolioUrl = safeWebUrl(product.portfolio_url);
    const portfolio = portfolioUrl
      ? '<div class="seller-hidden-link"><span>Портфоліо</span><a href="' +
        esc(portfolioUrl) + '" target="_blank" rel="noopener noreferrer">' +
        esc(portfolioUrl) + "</a></div>"
      : "";
    const image = product.main_image
      ? '<img class="seller-product-image" src="' + esc(product.main_image) + '" alt="">'
      : '<div class="seller-product-image is-empty" aria-hidden="true">Фото відсутнє</div>';
    const upload = usesDigitalDelivery(product) && !moderated
      ? '<form class="seller-upload-form" data-upload-form="' + product.id + '">' +
        '<label class="form-label">Файл для завантаження після покупки<input class="form-control form-control-sm mt-1" type="file" name="file" required></label>' +
        '<input class="form-control form-control-sm" name="title" placeholder="Назва файлу">' +
        '<label class="form-check seller-trial-check"><input class="form-check-input" type="checkbox" name="is_trial"> Пробний файл</label>' +
        '<p class="field-hint">Пробні файли доступні до покупки, інші файли покупець отримає після оплати.</p>' +
        '<button class="btn btn-outline-primary btn-sm" type="submit">Завантажити</button></form>'
      : "";
    const fileManagement = files || upload
      ? '<details class="seller-product-files"><summary>' +
        (files ? "Файли (" + digitalFiles.length + ")" : "Файли та завантаження") +
        '</summary><div class="seller-product-files-content">' +
        (files ? '<div class="seller-files">' + files + "</div>" : "") +
        upload + "</div></details>"
      : "";
    return '<article class="seller-product-card" data-seller-product-id="' +
      esc(product.id) + '">' + image +
      '<div class="seller-product-content"><div class="seller-product-top">' +
      '<div class="seller-product-info"><p class="seller-category-path mb-1">' +
      esc(product.category_path) + '</p><h3 class="h5 mb-2">' + esc(product.name) +
      '</h3><div class="seller-product-badges"><span class="badge-soft is-muted">' +
      esc(productTypeLabel(product.product_type)) + '</span><span class="badge-soft ' +
      (product.status === "active" ? "is-lime" : "is-muted") + '">' + esc(product.status) +
      "</span>" + moderationBadge + "</div>" + moderationReason + hiddenUrl + portfolio +
      '</div>' + statusButton + "</div>" +
      '<div class="seller-variant-summary-wrap"><h4>Ціни та залишки</h4><ul class="seller-variant-summary-list">' +
      variants + "</ul></div>" +
      fileManagement + "</div></article>";
  }

  function currentStatusFilter() {
    return $("sellerStatusFilter").value || "all";
  }

  function syncStatusUrl() {
    const params = new URLSearchParams(window.location.search);
    const status = currentStatusFilter();
    if (status === "all") params.delete("status");
    else params.set("status", status);
    const qs = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : ""));
  }

  async function loadProducts() {
    error("");
    const query = $("sellerSearch").value.trim();
    const status = currentStatusFilter();
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (status !== "all") params.set("status_filter", status);
    const url = "/products/mine" + (params.toString() ? "?" + params.toString() : "");
    const list = $("sellerProducts");
    const cacheKey = window.MSSkeletons.cacheKey(url);
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (productsAbortController) productsAbortController.abort();
    productsAbortController = new AbortController();
    const controller = productsAbortController;

    const renderData = (data) => {
      if (controller !== productsAbortController) return;
      const products = data.products || [];
      const hash = JSON.stringify(products.map((product) =>
        ({
          id: product.id,
          updated_at: product.updated_at,
          status: product.status,
          data: product,
        })
      ));
      if (hash === lastProductsHash) return;
      lastProductsHash = hash;
      debugStats.productsRender += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[seller] render списка:", debugStats.productsRender);
      }

      const existing = new Map(Array.from(list.querySelectorAll("[data-seller-product-id]")).map((card) => [
        String(card.dataset.sellerProductId),
        card,
      ]));
      const ids = new Set();
      products.forEach((product) => {
        const id = String(product.id);
        ids.add(id);
        const card = existing.get(id);
        const html = renderProduct(product);
        if (card) {
          const signature = JSON.stringify(product);
          if (card.dataset.productSignature !== signature) {
            card.innerHTML = html.replace(/^<article[^>]*>|<\/article>$/g, "");
            card.dataset.productSignature = signature;
          }
        } else {
          const template = document.createElement("template");
          template.innerHTML = html.trim();
          const newCard = template.content.firstElementChild;
          newCard.dataset.productSignature = JSON.stringify(product);
          list.appendChild(newCard);
        }
      });
      existing.forEach((card, id) => {
        if (!ids.has(id)) card.remove();
      });

      const empty = $("sellerProductsEmpty");
      empty.hidden = products.length > 0;
      empty.textContent = (query || status !== "all")
        ? "Нічого не знайдено за запитом"
        : "Товарів ще немає.";
      bindProductActions();
    };

    if (cached !== undefined) renderData(cached);
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton(list, "seller", 3)
      : null;
    try {
      debugStats.productsFetch += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[seller] fetch списка:", debugStats.productsFetch);
      }
      const data = await window.MS.get(url, { signal: controller.signal });
      window.MSSkeletons.setCachedData(cacheKey, data);
      if (loading) await loading.finish(() => renderData(data));
      else renderData(data);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити товари продавця.");
      if (loading) {
        await loading.finish(() => {
          list.replaceChildren();
          $("sellerProductsEmpty").hidden = true;
          window.MSSkeletons.renderRetryError($("sellerError"), message, loadProducts, true);
        });
      } else {
        window.MSSkeletons.renderRetryError($("sellerError"), message, loadProducts, true);
      }
    }
  }

  function bindProductActions() {
    document.querySelectorAll("[data-toggle-status]").forEach((button) => {
      if (button.dataset.actionBound === "true") return;
      button.dataset.actionBound = "true";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await window.MS.request("/products/mine/" + button.dataset.toggleStatus, {
            method: "PATCH",
            body: { status: button.dataset.status },
          });
          success("Статус товару оновлено.");
          await loadProducts();
        } catch (err) {
          error(err.message || "Не вдалося змінити статус.");
          button.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-restock-form]").forEach((form) => {
      if (form.dataset.actionBound === "true") return;
      form.dataset.actionBound = "true";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const quantity = Number(form.querySelector("[data-restock-quantity]").value);
        if (!Number.isSafeInteger(quantity) || quantity < 1) {
          error("Вкажіть цілу кількість більше нуля.");
          return;
        }
        const payload = { quantity: quantity };
        const codesInput = form.querySelector("[data-restock-codes]");
        if (codesInput) {
          payload.activation_codes = codesInput.value
            .split(/\r?\n/).map((code) => code.trim()).filter(Boolean);
        }
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
          const result = await window.MS.post(
            "/products/mine/" + form.dataset.productId + "/variants/" +
              form.dataset.variantId + "/restock",
            payload
          );
          success(
            "Залишок поповнено. Додаткова застава: " +
              fmt(result.additional_deposit_mc) + " MC."
          );
          await loadProducts();
        } catch (err) {
          error(err.message || "Не вдалося поповнити залишок.");
          button.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-price-form]").forEach((form) => {
      if (form.dataset.actionBound === "true") return;
      form.dataset.actionBound = "true";
      form.querySelectorAll("[data-price-step]").forEach((stepButton) => {
        stepButton.addEventListener("click", () => {
          const input = form.querySelector("[data-price-value]");
          const current = priceToMicros(input.value) ||
            priceToMicros(input.dataset.originalPrice) || 1;
          const next = Math.max(
            1,
            Math.min(MAX_PRICE_MICROS, current + Number(stepButton.dataset.priceStep))
          );
          input.value = microsToPrice(next);
        });
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = form.querySelector("[data-price-value]");
        const priceMicros = priceToMicros(input.value);
        if (priceMicros === null) {
          error("Вкажіть ціну від 0,000001 до 999 999 999 MC і не більше шести знаків після коми.");
          input.focus();
          return;
        }

        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
          const result = await window.MS.post(
            "/products/mine/" + form.dataset.productId + "/variants/" +
              form.dataset.variantId + "/price",
            { price_mc: microsToPrice(priceMicros).replace(",", ".") }
          );
          if (!result.price_changed) {
            success("Ціна не змінилася.");
          } else if (Number(result.additional_deposit_mc) > 0) {
            success(
              "Ціну змінено. Додаткову заставу заблоковано: " +
                fmt(result.additional_deposit_mc) + " MC."
            );
          } else if (Number(result.returned_deposit_mc) > 0) {
            success(
              "Ціну змінено. Надлишок застави " +
                fmt(result.returned_deposit_mc) + " MC заморожено на " +
                result.return_delay_days + " днів."
            );
          } else {
            success("Ціну змінено. Застава не змінилася.");
          }
          await loadProducts();
        } catch (err) {
          error(err.message || "Не вдалося змінити ціну.");
          button.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-upload-form]").forEach((form) => {
      if (form.dataset.actionBound === "true") return;
      form.dataset.actionBound = "true";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await window.MS.post(
            "/products/mine/" + form.dataset.uploadForm + "/digital-files",
            new FormData(form)
          );
          success("Файл завантажено до приватного сховища.");
          await loadProducts();
        } catch (err) {
          error(err.message || "Не вдалося завантажити файл.");
        }
      });
    });
    document.querySelectorAll("[data-remove-file]").forEach((button) => {
      if (button.dataset.actionBound === "true") return;
      button.dataset.actionBound = "true";
      button.addEventListener("click", async () => {
        try {
          await window.MS.del(
            "/products/mine/" + button.dataset.productId +
            "/digital-files?file_id=" + button.dataset.removeFile
          );
          await loadProducts();
        } catch (err) {
          error(err.message || "Файл не можна видалити.");
        }
      });
    });
  }

  function categoryUrl(parentId) {
    const params = new URLSearchParams();
    if (categoryState.view) params.set("view", categoryState.view);
    if (parentId) params.set("parent", String(parentId));
    return "/catalog/categories?" + params.toString();
  }

  function renderTreeViews() {
    const views = [
      { key: "physical", label: "Фізичні" },
      { key: "digital", label: "Цифрові" },
      { key: "services", label: "Послуги й підписки" },
    ];
    const wrap = $("sellerTreeViews");
    wrap.innerHTML = "";
    views.forEach((view) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "view-tab" + (categoryState.view === view.key ? " is-active" : "");
      button.textContent = view.label;
      button.addEventListener("click", () => {
        if (categoryState.view === view.key) return;
        // Вкладки лише для навігації: тип товару не змінюють
        categoryState.view = view.key;
        categoryState.path = [];
        categoryState.pending = null;
        renderTreeViews();
        loadCategoryChildren(null);
      });
      wrap.appendChild(button);
    });
  }

  function renderTreeBreadcrumbs() {
    const wrap = $("sellerTreeBreadcrumbs");
    wrap.innerHTML = "";
    const root = document.createElement("button");
    root.type = "button";
    root.textContent = "Усі розділи";
    root.addEventListener("click", () => {
      categoryState.path = [];
      categoryState.pending = null;
      loadCategoryChildren(null);
    });
    wrap.appendChild(root);
    categoryState.path.forEach((node, index) => {
      const separator = document.createElement("span");
      separator.className = "crumbs-sep";
      separator.textContent = "›";
      wrap.appendChild(separator);
      const crumb = document.createElement("button");
      crumb.type = "button";
      crumb.textContent = node.name;
      crumb.addEventListener("click", () => {
        categoryState.path = categoryState.path.slice(0, index + 1);
        categoryState.pending = null;
        loadCategoryChildren(node.id);
      });
      wrap.appendChild(crumb);
    });
  }

  function updatePendingCategory() {
    const pending = categoryState.pending;
    $("sellerTreeChoose").disabled = !pending;
    $("sellerTreeSelection").textContent = pending
      ? pending.path
      : "Оберіть кінцеву категорію.";
    $("sellerTreeHint").textContent = pending
      ? "Це кінцева категорія. Підтвердіть вибір або поверніться до списку."
      : "Перейдіть до кінцевої категорії товару.";
  }

  async function loadCategoryChildren(parentId) {
    $("sellerTreeTiles").innerHTML = '<div class="seller-tree-loading">Завантаження розділів…</div>';
    renderTreeBreadcrumbs();
    updatePendingCategory();
    try {
      const data = await window.MS.get(categoryUrl(parentId));
      const categories = data.categories || [];
      const tiles = $("sellerTreeTiles");
      tiles.innerHTML = "";
      if (!categories.length) {
        tiles.innerHTML = '<p class="seller-tree-empty">У цьому розділі поки немає підкатегорій.</p>';
        return;
      }
      categories.forEach((category) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "tile seller-tree-tile";
        button.innerHTML = '<span class="tile-name">' + esc(category.name) + "</span>" +
          (category.has_children
            ? '<span class="seller-tree-tile-arrow" aria-hidden="true">›</span>'
            : '<span class="seller-tree-terminal">Кінцева категорія</span>');
        button.addEventListener("click", async () => {
          categoryState.pending = null;
          if (!categoryState.path.length) {
            // Корінь гілки визначає тип товару
            categoryState.rootTypes = category.product_types || typesForView(categoryState.view);
          }
          if (category.has_children) {
            categoryState.path.push(category);
            await loadCategoryChildren(category.id);
            return;
          }
          try {
            const suffix = categoryState.view
              ? "?view=" + encodeURIComponent(categoryState.view)
              : "";
            const popup = await window.MS.get(
              "/catalog/categories/" + category.id + "/popup" + suffix
            );
            if ((popup.children || []).length) {
              categoryState.path.push(category);
              await loadCategoryChildren(category.id);
              return;
            }
            categoryState.pending = {
              id: category.id,
              name: popup.category.name,
              path: popup.category.path,
              attributes: cleanSellerAttributes(popup.attributes),
            };
            updatePendingCategory();
            tiles.querySelectorAll(".seller-tree-tile").forEach((tile) => {
              tile.classList.remove("is-selected");
            });
            button.classList.add("is-selected");
          } catch (err) {
            window.MS.toast(err.message || "Не вдалося перевірити категорію.", "danger");
          }
        });
        tiles.appendChild(button);
      });
      renderTreeBreadcrumbs();
    } catch (err) {
      $("sellerTreeTiles").innerHTML =
        '<p class="seller-tree-empty">Не вдалося завантажити категорії. Спробуйте ще раз.</p>';
      window.MS.toast(err.message || "Не вдалося завантажити категорії.", "danger");
    }
  }

  function openCategoryPicker() {
    categoryState.view = viewForType(categoryState.productType);
    categoryState.path = [];
    categoryState.pending = null;
    renderTreeViews();
    $("sellerCategoryDialog").hidden = false;
    document.body.classList.add("seller-dialog-open");
    document.documentElement.classList.add("seller-dialog-open");
    loadCategoryChildren(null);
    $("sellerTreeClose").focus();
  }

  function closeCategoryPicker() {
    $("sellerCategoryDialog").hidden = true;
    document.body.classList.remove("seller-dialog-open");
    document.documentElement.classList.remove("seller-dialog-open");
    $("categoryPicker").focus();
  }

  function chooseCategory() {
    if (!categoryState.pending) return;
    categoryState.selected = categoryState.pending;
    categoryState.rootTypes = categoryState.rootTypes.length
      ? categoryState.rootTypes
      : typesForView(categoryState.view);
    categoryState.productType = categoryState.rootTypes[0] || "";
    $("productCategory").value = String(categoryState.selected.id);
    $("selectedCategoryLabel").textContent = categoryState.selected.name;
    const oneTimeDevelopment = isOneTimeDevelopmentPath(categoryState.selected.path);
    $("selectedCategoryPath").textContent = categoryState.selected.path +
      (oneTimeDevelopment ? " · цю роботу можна продати лише один раз." : "");
    categoryState.attributes = categoryState.selected.attributes;
    updateTypeDisplay();
    renderVariantRows();
    closeCategoryPicker();
  }

  function clearSelectedCategory(message) {
    categoryState.selected = null;
    categoryState.rootTypes = [];
    categoryState.attributes = [];
    categoryState.productType = "";
    $("productCategory").value = "";
    $("selectedCategoryLabel").textContent = "Оберіть категорію в каталозі";
    $("selectedCategoryPath").textContent = message || "Доступні лише кінцеві категорії.";
    updateTypeDisplay();
    renderVariantRows();
  }

  function renderAttributeControl(attribute) {
    const id = String(attribute.id);
    const label = attribute.name + (attribute.unit ? " (" + attribute.unit + ")" : "");
    const required = attribute.required ? " required" : "";
    let control = "";
    if (attribute.kind === "number") {
      control = '<input class="form-control form-control-sm" type="number" step="any" data-attribute-value="' +
        id + '"' + required + ' placeholder="Значення">';
    } else if (attribute.kind === "multiple") {
      const values = (attribute.options || []).map((option) =>
        '<label class="seller-option-chip"><input type="checkbox" data-attribute-value="' +
        id + '" value="' + esc(option.value) + '"><span>' + esc(option.value) + "</span></label>"
      ).join("");
      control = '<div class="seller-option-list" data-attribute-group="' + id + '">' +
        values + "</div>" +
        (attribute.required ? '<span class="field-hint" data-required-hint="' + id +
          '" hidden>Оберіть хоча б одне значення.</span>' : "");
    } else {
      const options = attribute.kind === "bool"
        ? [{ value: "так" }, { value: "ні" }]
        : (attribute.options || []);
      control = '<select class="form-select form-select-sm" data-attribute-value="' + id +
        '"' + required + '><option value="">Не вказано</option>' +
        options.map((option) => '<option value="' + esc(option.value) + '">' +
          esc(option.value) + "</option>").join("") + "</select>";
    }
    return '<div class="seller-attribute-field"><label class="form-label">' +
      esc(label) + (attribute.required ? ' <span class="seller-required">*</span>' : "") +
      "</label>" + control + "</div>";
  }

  function readVariantRows() {
    return Array.from($("productVariants").querySelectorAll("[data-variant-row]")).map((row) => ({
      sku: row.querySelector("[data-variant-sku]").value,
      price: row.querySelector("[data-variant-price]").value,
      stock: row.querySelector("[data-variant-stock]").value,
      license: row.querySelector("[data-variant-license]").value,
      activationCodes: row.querySelector("[data-activation-codes]")
        ? row.querySelector("[data-activation-codes]").value.split(/\r?\n/)
          .map((code) => code.trim()).filter(Boolean)
        : [],
      paidFile: (row._files && row._files.paid) || null,
      trialFile: (row._files && row._files.trial) || null,
      attributes: categoryState.attributes.map((attribute) => {
        const controls = row.querySelectorAll('[data-attribute-value="' + attribute.id + '"]');
        if (attribute.kind === "multiple") {
          return Array.from(controls).filter((control) => control.checked).map((control) => control.value);
        }
        return controls.length ? controls[0].value : "";
      }),
    }));
  }

  function renderVariantRows(valuesOverride) {
    const list = $("productVariants");
    const values = valuesOverride || readVariantRows();
    const oneTimeDevelopment = categoryState.selected &&
      isOneTimeDevelopmentPath(categoryState.selected.path);
    const needsFiles = requiresDigitalFiles();
    const hasDeliveryAttribute = categoryState.attributes.some((attribute) =>
      attribute.name === "Видача"
    );
    const supportsActivationCodes = hasDeliveryAttribute &&
      ["digital_key", "subscription"].includes(categoryState.productType);
    const count = oneTimeDevelopment ? 1 : Math.max(1, values.length);
    $("addVariantButton").disabled = !!oneTimeDevelopment;
    $("addVariantButton").title = oneTimeDevelopment
      ? "Готову розробку можна продати лише одному покупцю."
      : "";
    list.innerHTML = "";
    for (let i = 0; i < count; i += 1) {
      const value = values[i] ||
        { sku: "", price: "", stock: "1", license: "", attributes: [], activationCodes: [], paidFile: null, trialFile: null };
      const row = document.createElement("article");
      row.className = "seller-variant-card";
      row.dataset.variantRow = "true";
      row.innerHTML = '<div class="seller-variant-heading"><div><span class="seller-variant-number"></span>' +
        '<span class="field-hint">' +
        (oneTimeDevelopment
          ? "Готову розробку може придбати лише один покупець."
          : "Ціна, залишок і характеристики цього варіанта") +
        "</span></div>" +
        '<button class="btn btn-link btn-sm text-danger" type="button" data-remove-variant>Видалити</button></div>' +
        '<div class="row g-3"><div class="col-md-3"><label class="form-label">Артикул / SKU</label>' +
        '<input class="form-control" maxlength="64" data-variant-sku placeholder="Необов’язково"></div>' +
        '<div class="col-md-3"><label class="form-label">Ціна, MC</label>' +
        '<input class="form-control" type="number" min="0.000001" step="any" data-variant-price required placeholder="100"></div>' +
        '<div class="col-md-3"><label class="form-label">Залишок</label>' +
        '<input class="form-control" type="number" min="0" step="1" data-variant-stock required value="1"></div>' +
        '<div class="col-md-3" data-license-field><label class="form-label">Ліцензія</label><select class="form-select" data-variant-license>' +
        '<option value="">Не застосовується</option><option value="personal">Персональна</option>' +
        '<option value="commercial">Комерційна</option></select></div></div>' +
        (categoryState.attributes.length
          ? '<div class="seller-variant-attributes"><h4>Характеристики</h4><div class="seller-attribute-grid">' +
            categoryState.attributes.map(renderAttributeControl).join("") + "</div></div>"
          : "") +
        (supportsActivationCodes
          ? '<div class="seller-activation-code-field" data-activation-field hidden>' +
            '<label class="form-label">Коди активації</label>' +
            '<textarea class="form-control" rows="3" maxlength="256000" data-activation-codes ' +
            'placeholder="Один код на рядок"></textarea>' +
            '<div class="field-hint" data-activation-status>Додайте окремий унікальний код на кожну одиницю. Покупець побачить їх після оплати.</div>' +
            "</div>"
          : "") +
        (needsFiles
          ? '<div class="seller-variant-files"><h4>Файли товару</h4>' +
            '<div class="seller-file-input"><label class="form-label">Файл для покупця</label>' +
            '<input class="form-control form-control-sm" type="file" data-paid-file>' +
            '<div class="field-hint seller-file-status" data-paid-status hidden></div></div>' +
            '<div class="seller-file-input"><label class="form-label">Пробний файл (доступний усім)</label>' +
            '<input class="form-control form-control-sm" type="file" data-trial-file>' +
            '<div class="field-hint seller-file-status" data-trial-status hidden></div></div></div>'
          : "");
      row.querySelector("[data-variant-sku]").value = value.sku;
      row.querySelector("[data-variant-price]").value = value.price;
      const stockInput = row.querySelector("[data-variant-stock]");
      stockInput.value = oneTimeDevelopment ? "1" : value.stock;
      stockInput.readOnly = !!oneTimeDevelopment;
      row.querySelector("[data-variant-license]").value = value.license;
      const activationCodesInput = row.querySelector("[data-activation-codes]");
      if (activationCodesInput) activationCodesInput.value = (value.activationCodes || []).join("\n");
      if (needsFiles) {
        row._files = { paid: value.paidFile || null, trial: value.trialFile || null };
        bindFileInput(row, "paid");
        bindFileInput(row, "trial");
      }
      const attributeControls = row.querySelectorAll("[data-attribute-value]");
      let offset = 0;
      categoryState.attributes.forEach((attribute, index) => {
        const attributeValue = value.attributes[index];
        if (attribute.kind === "multiple") {
          const selected = Array.isArray(attributeValue) ? attributeValue : [];
          row.querySelectorAll('[data-attribute-value="' + attribute.id + '"]').forEach((control) => {
            control.checked = selected.includes(control.value);
          });
          offset += row.querySelectorAll('[data-attribute-value="' + attribute.id + '"]').length;
        } else if (attributeControls[offset]) {
          attributeControls[offset].value = attributeValue || "";
          offset += 1;
        }
      });
      list.appendChild(row);
    }
    updateVariantHeadings();
    bindVariantActions();
    updateLicenseFields();
    updateActivationCodeFields();
  }

  function bindFileInput(row, which) {
    const input = row.querySelector(which === "paid" ? "[data-paid-file]" : "[data-trial-file]");
    if (!input) return;
    const status = row.querySelector(which === "paid" ? "[data-paid-status]" : "[data-trial-status]");
    const render = () => {
      const file = row._files ? row._files[which] : null;
      status.hidden = !file;
      status.textContent = "";
      if (!file) return;
      status.append(document.createTextNode(file.name + " · " + formatFileSize(file.size) + " "));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-link btn-sm text-danger p-0";
      remove.textContent = "Прибрати";
      remove.addEventListener("click", () => {
        row._files[which] = null;
        input.value = "";
        render();
      });
      status.appendChild(remove);
    };
    input.addEventListener("change", () => {
      if (!row._files) row._files = { paid: null, trial: null };
      row._files[which] = input.files[0] || null;
      render();
    });
    render();
  }

  function updateLicenseFields() {
    const type = categoryState.productType;
    const digital = type === "digital_file" || type === "digital_key";
    $("productVariants").querySelectorAll("[data-license-field]").forEach((field) => {
      field.hidden = !digital;
    });
    $("portfolioField").hidden = type !== "service";
    $("digitalFilesSection").hidden = !requiresDigitalFiles();
  }

  function variantUsesActivationCode(row) {
    const delivery = categoryState.attributes.find((attribute) =>
      attribute.name === "Видача"
    );
    if (!delivery) return false;
    const control = row.querySelector('[data-attribute-value="' + delivery.id + '"]');
    return !!control && (
      control.value === "Код активації" ||
      (categoryState.productType === "subscription" && control.value === "Ключ")
    );
  }

  function activationCodeCountText(count) {
    const lastTwo = count % 100;
    const last = count % 10;
    const noun = last === 1 && lastTwo !== 11
      ? "код"
      : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)
        ? "коди"
        : "кодів";
    return count + " " + noun;
  }

  function updateActivationCodeFields() {
    $("productVariants").querySelectorAll("[data-variant-row]").forEach((row) => {
      const field = row.querySelector("[data-activation-field]");
      if (!field) return;
      const visible = variantUsesActivationCode(row);
      field.hidden = !visible;
      if (!visible) return;
      const count = row.querySelector("[data-activation-codes]").value
        .split(/\r?\n/).map((code) => code.trim()).filter(Boolean).length;
      const stock = Number(row.querySelector("[data-variant-stock]").value);
      const status = field.querySelector("[data-activation-status]");
      const input = field.querySelector("[data-activation-codes]");
      if (!input.classList.contains("is-invalid") && !input.classList.contains("is-valid")) {
        status.textContent = "Введіть стільки кодів, скільки одиниць у залишку, — по одному на рядок. Покупець побачить коди після оплати.";
      } else if (count !== stock) {
        status.textContent = "Потрібно: " + activationCodeCountText(stock) +
          ". Введено: " + activationCodeCountText(count) + ".";
      }
    });
  }

  async function validateActivationCodes() {
    const fields = Array.from(
      $("productVariants").querySelectorAll("[data-activation-field]")
    ).filter((field) => !field.hidden);
    const states = fields.map((field) => {
      const codes = field.querySelector("[data-activation-codes]").value
        .split(/\r?\n/).map((code) => code.trim()).filter(Boolean);
      const stock = Number(field.closest("[data-variant-row]")
        .querySelector("[data-variant-stock]").value);
      return { field: field, codes: codes, stock: stock, valid: codes.length === stock };
    });
    const seen = new Set();
    const duplicates = new Set();
    states.forEach((state) => state.codes.forEach((code) => {
      const key = code.normalize("NFKC").toLowerCase();
      if (seen.has(key)) duplicates.add(key);
      seen.add(key);
    }));
    const localDuplicateFields = new Set();
    states.forEach((state) => {
      if (state.codes.some((code) => duplicates.has(code.normalize("NFKC").toLowerCase()))) {
        localDuplicateFields.add(state.field);
      }
    });
    const codes = states.reduce((all, state) => all.concat(state.codes), []);
    const version = ++activationCheckVersion;
    let availability = [];
    if (codes.length) {
      const result = await window.MS.post("/products/mine/activation-codes/check", {
        codes: codes,
      });
      if (version !== activationCheckVersion) return false;
      availability = result.available || [];
      if (availability.length !== codes.length) {
        throw new Error("Не вдалося перевірити унікальність кодів.");
      }
    }

    let index = 0;
    let allValid = true;
    states.forEach((state) => {
      const values = availability.slice(index, index + state.codes.length);
      index += state.codes.length;
      const duplicatedLocally = localDuplicateFields.has(state.field);
      const duplicatedGlobally = values.some((available) => !available);
      const valid = state.valid && !duplicatedLocally && !duplicatedGlobally;
      const input = state.field.querySelector("[data-activation-codes]");
      const status = state.field.querySelector("[data-activation-status]");
      input.classList.toggle("is-valid", valid);
      input.classList.toggle("is-invalid", !valid);
      if (!state.valid) {
        status.textContent = "Потрібно: " + activationCodeCountText(state.stock) +
          ". Введено: " + activationCodeCountText(state.codes.length) + ".";
      } else if (duplicatedLocally) {
        status.textContent = "Код повторюється в цьому оголошенні.";
      } else if (duplicatedGlobally) {
        status.textContent = "Один або кілька кодів уже додані до іншого товару.";
      } else {
        status.textContent = "Усі " + state.codes.length + " кодів унікальні.";
      }
      if (!valid) allValid = false;
    });
    return allValid;
  }

  function scheduleActivationCodeCheck() {
    window.clearTimeout(activationCheckTimer);
    activationCheckTimer = window.setTimeout(() => {
      validateActivationCodes().then((valid) => {
        if (valid) error("");
      }).catch((err) => {
        $("productVariants").querySelectorAll("[data-activation-field]:not([hidden])").forEach((field) => {
          const input = field.querySelector("[data-activation-codes]");
          input.classList.remove("is-valid");
          input.classList.add("is-invalid");
          field.querySelector("[data-activation-status]").textContent =
            "Не вдалося перевірити коди. Спробуйте ще раз.";
        });
        error(err.message || "Не вдалося перевірити коди активації.");
      });
    }, 350);
  }

  function updateVariantHeadings() {
    const rows = Array.from($("productVariants").querySelectorAll("[data-variant-row]"));
    rows.forEach((row, index) => {
      row.querySelector(".seller-variant-number").textContent = "Варіант " + (index + 1);
      const remove = row.querySelector("[data-remove-variant]");
      remove.hidden = rows.length === 1;
    });
  }

  function bindVariantActions() {
    $("productVariants").querySelectorAll("[data-remove-variant]").forEach((button) => {
      button.addEventListener("click", () => {
        button.closest("[data-variant-row]").remove();
        updateVariantHeadings();
      });
    });
  }

  function collectVariants() {
    const result = [];
    const rows = Array.from($("productVariants").querySelectorAll("[data-variant-row]"));
    const oneTimeDevelopment = categoryState.selected &&
      isOneTimeDevelopmentPath(categoryState.selected.path);
    rows.forEach((row) => {
      const attributes = [];
      categoryState.attributes.forEach((attribute) => {
        const controls = row.querySelectorAll('[data-attribute-value="' + attribute.id + '"]');
        let values = [];
        if (attribute.kind === "multiple") {
          values = Array.from(controls)
            .filter((control) => control.checked)
            .map((control) => control.value);
          const hint = row.querySelector('[data-required-hint="' + attribute.id + '"]');
          if (hint) hint.hidden = !attribute.required || values.length > 0;
          if (attribute.required && !values.length) {
            throw new Error("Оберіть значення характеристики «" + attribute.name + "».");
          }
        } else if (controls.length && controls[0].value) {
          values = [controls[0].value];
        }
        if (attribute.required && !values.length) {
          throw new Error("Заповніть характеристику «" + attribute.name + "».");
        }
        values.forEach((value) => attributes.push({
          attribute_id: attribute.id,
          value: value,
        }));
      });
      result.push({
        sku: row.querySelector("[data-variant-sku]").value.trim(),
        price_mc: row.querySelector("[data-variant-price]").value,
        stock: oneTimeDevelopment ? 1 : Number(row.querySelector("[data-variant-stock]").value),
        license_type: row.querySelector("[data-variant-license]").value,
        activation_codes: row.querySelector("[data-activation-field]") &&
          !row.querySelector("[data-activation-field]").hidden
          ? row.querySelector("[data-activation-codes]").value
            .split(/\r?\n/).map((code) => code.trim()).filter(Boolean)
          : [],
        attributes: attributes,
      });
    });
    return result;
  }

  // Вибрані файли за порядком варіантів: { paid, trial }
  function collectVariantFiles() {
    return Array.from($("productVariants").querySelectorAll("[data-variant-row]")).map((row) => ({
      paid: (row._files && row._files.paid) || null,
      trial: (row._files && row._files.trial) || null,
    }));
  }

  function resetProductForm() {
    $("productForm").reset();
    $("productCategory").value = "";
    $("selectedCategoryLabel").textContent = "Оберіть категорію в каталозі";
    $("selectedCategoryPath").textContent = "Доступні лише кінцеві категорії.";
    categoryState.selected = null;
    categoryState.rootTypes = [];
    categoryState.attributes = [];
    categoryState.productType = "";
    updateTypeDisplay();
    $("productVariants").innerHTML = "";
    renderVariantRows();
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    imagePreviewUrl = "";
    $("productImagePreview").hidden = true;
    $("productImagePreview").removeAttribute("src");
  }

  function formHasData() {
    if ($("productName").value.trim()) return true;
    if ($("productDescription").value.trim()) return true;
    if ($("productBrand").value.trim()) return true;
    if ($("productPortfolioUrl").value.trim()) return true;
    if ($("productCategory").value) return true;
    if ($("productImage").files.length) return true;
    return Array.from($("productVariants").querySelectorAll("[data-variant-row]")).some((row) => {
      if (row.querySelector("[data-variant-sku]").value.trim()) return true;
      if (row.querySelector("[data-variant-price]").value.trim()) return true;
      return Array.from(row.querySelectorAll("[data-attribute-value]")).some((control) =>
        control.type === "checkbox" ? control.checked : control.value.trim() !== ""
      );
    });
  }

  function openProductForm() {
    $("productFormPanel").hidden = false;
    $("productFormPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function closeProductForm() {
    if (formHasData() && !window.confirm("Скинути введені дані?")) return;
    resetProductForm();
    $("productFormPanel").hidden = true;
  }

  function toggleProductForm() {
    if ($("productFormPanel").hidden) openProductForm();
    else closeProductForm();
  }

  function runSearch() {
    loadProducts().catch((err) => error(err.message || "Не вдалося завантажити товари."));
  }

  function bindSellerList() {
    if ($("sellerStatusFilter").dataset.listBound === "true") return;
    $("sellerStatusFilter").dataset.listBound = "true";
    sellerSearchTimer = 0;
    // Фільтр статусу відновлюємо з URL, щоб не губився після перезавантаження
    const statusParam = new URLSearchParams(window.location.search).get("status");
    if (statusParam === "active" || statusParam === "inactive") {
      $("sellerStatusFilter").value = statusParam;
    }
    const search = $("sellerSearch");
    search.addEventListener("input", () => {
      window.clearTimeout(sellerSearchTimer);
      sellerSearchTimer = window.setTimeout(runSearch, 300);
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        window.clearTimeout(sellerSearchTimer);
        runSearch();
      }
    });
    $("sellerSearchBtn").addEventListener("click", () => {
      window.clearTimeout(sellerSearchTimer);
      runSearch();
    });
    $("sellerStatusFilter").addEventListener("change", () => {
      syncStatusUrl();
      runSearch();
    });
    $("addProductButton").addEventListener("click", toggleProductForm);
    $("cancelProductButton").addEventListener("click", closeProductForm);
  }

  function bindProductForm() {
    document.body.appendChild($("sellerCategoryDialog"));
    $("categoryPicker").addEventListener("click", openCategoryPicker);
    $("sellerTreeClose").addEventListener("click", closeCategoryPicker);
    $("sellerTreeChoose").addEventListener("click", chooseCategory);
    $("sellerCategoryDialog").addEventListener("click", (event) => {
      if (event.target === $("sellerCategoryDialog")) closeCategoryPicker();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("sellerCategoryDialog").hidden) closeCategoryPicker();
    });
    $("addVariantButton").addEventListener("click", () => {
      const values = readVariantRows();
      values.push({ sku: "", price: "", stock: "1", license: "", attributes: [], activationCodes: [], paidFile: null, trialFile: null });
      renderVariantRows(values);
    });
    $("productVariants").addEventListener("input", (event) => {
      if (event.target.matches("[data-activation-codes]")) {
        scheduleActivationCodeCheck();
      } else if (event.target.matches("[data-variant-stock]")) {
        updateActivationCodeFields();
        scheduleActivationCodeCheck();
      }
    });
    $("productVariants").addEventListener("change", (event) => {
      if (event.target.matches("[data-attribute-value]")) {
        updateActivationCodeFields();
        scheduleActivationCodeCheck();
      }
    });
    $("productImage").addEventListener("change", () => {
      const file = $("productImage").files[0];
      const preview = $("productImagePreview");
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      imagePreviewUrl = file ? URL.createObjectURL(file) : "";
      preview.src = imagePreviewUrl;
      preview.hidden = !imagePreviewUrl;
    });
    $("productForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      error("");
      if (!$("productCategory").value) {
        error("Оберіть кінцеву категорію товару.");
        $("categoryPicker").focus();
        return;
      }
      try {
        if (!await validateActivationCodes()) {
          error("Перевірте кількість і унікальність кодів активації.");
          const invalidCodeInput = $("productVariants")
            .querySelector("[data-activation-codes].is-invalid");
          if (invalidCodeInput) invalidCodeInput.focus();
          return;
        }
      } catch (err) {
        error(err.message || "Не вдалося перевірити коди активації.");
        return;
      }
      let variants;
      try {
        variants = collectVariants();
      } catch (err) {
        error(err.message);
        return;
      }
      const needsFiles = requiresDigitalFiles();
      const files = needsFiles ? collectVariantFiles() : [];
      const chosenStatus = $("productStatus").value;
      const payload = new FormData();
      payload.append("category_id", $("productCategory").value);
      payload.append("name", $("productName").value.trim());
      payload.append("description", $("productDescription").value.trim());
      payload.append("brand", $("productBrand").value.trim());
      payload.append("product_type", categoryState.productType);
      if (categoryState.productType === "service") {
        payload.append("portfolio_url", $("productPortfolioUrl").value.trim());
      }
      // Товар із файлами публікуємо після завантаження: спершу inactive
      payload.append("status", needsFiles ? "inactive" : chosenStatus);
      payload.append("variants", JSON.stringify(variants));
      const image = $("productImage").files[0];
      if (image) payload.append("main_image", image);

      const button = $("createProductButton");
      button.disabled = true;
      button.textContent = "Створюємо…";
      let created;
      try {
        created = await window.MS.post("/products/mine", payload);
      } catch (err) {
        error(err.message || "Не вдалося створити товар.");
        button.disabled = false;
        button.textContent = "Створити товар";
        return;
      }

      let publishFailed = false;
      if (needsFiles) {
        button.textContent = "Завантаження…";
        const variantIds = (created.variants || []).map((variant) => variant.id);
        // Бекенд віддає варіанти за зростанням ціни — вирівнюємо порядок файлів
        const pairs = variants.map((variant, index) => ({
          price: Number(variant.price_mc) || 0,
          files: files[index] || { paid: null, trial: null },
        })).sort((a, b) => a.price - b.price);
        try {
          for (let i = 0; i < pairs.length; i += 1) {
            const pair = pairs[i].files;
            const variantId = variantIds[i];
            if (pair.paid) {
              const data = new FormData();
              data.append("file", pair.paid);
              if (variantId) data.append("variant_id", String(variantId));
              await window.MS.post("/products/mine/" + created.id + "/digital-files", data);
            }
            if (pair.trial) {
              const data = new FormData();
              data.append("file", pair.trial);
              if (variantId) data.append("variant_id", String(variantId));
              data.append("is_trial", "true");
              await window.MS.post("/products/mine/" + created.id + "/digital-files", data);
            }
          }
          if (chosenStatus !== "inactive") {
            await window.MS.request("/products/mine/" + created.id, {
              method: "PATCH",
              body: { status: chosenStatus },
            });
          }
        } catch (_err) {
          publishFailed = true;
        }
      }

      button.disabled = false;
      button.textContent = "Створити товар";
      if (publishFailed) {
        // Товар лишається inactive; форма не скидається, щоб дані не загубились
        error("Товар створено, але файл не завантажено. Додайте файл у картці товару.");
        await loadProducts();
        return;
      }
      if (needsFiles && chosenStatus !== "inactive") {
        success("Товар створено та опубліковано з файлами.");
      } else if (needsFiles) {
        success("Товар створено зі статусом «Зупинений».");
      } else {
        success("Товар створено, потрібну заставу заблоковано.");
      }
      resetProductForm();
      $("productFormPanel").hidden = true;
      await loadProducts();
    });
  }

  function bindVerificationForm() {
    $("verificationForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      error("");
      const formData = new FormData();
      formData.append("age", $("sellerAge").value);
      formData.append("photo", $("sellerPhoto").files[0]);
      const button = $("verifyButton");
      button.disabled = true;
      try {
        const result = await window.MS.post("/profile/seller-verification", formData);
        success("Верифікацію подано й заблоковано від редагування.");
        writeVerificationCache(window.MSAuth.state.user.id, result.age);
        showVerified(result.age, true);
        await window.MSAuth.refresh({ strict: true });
        renderVariantRows();
        await loadProducts();
      } catch (err) {
        error(err.message || "Не вдалося подати верифікацію.");
        button.disabled = false;
      }
    });
  }

  async function loadVerificationState(retry = false, dataOverride = null) {
    const user = window.MSAuth.state.user;
    if (!user || !user.authenticated) return;

    clearOtherVerificationCaches(user.id);
    const cached = readVerificationCache(user.id);
    if (!retry && cached) {
      showVerified(cached.age);
      renderVariantRows();
    } else if (!retry && dataOverride == null) {
      showVerificationLoading();
    }

    if (dataOverride) {
      const data = dataOverride;
      if (data.seller_verified) {
        writeVerificationCache(user.id, data.verified_age);
        showVerified(data.verified_age);
        renderVariantRows();
      } else if (Number.isInteger(data.seller_verification_age) &&
                 data.seller_verification_age < 18) {
        $("verificationInstructions").hidden = true;
        $("verificationPanel").classList.remove("is-loading");
        $("verificationStatus").innerHTML =
          '<div class="verification-underage">Продаж заборонено: продавцем можна бути лише з 18 років. Дані не збережено.</div>';
        $("verificationForm").hidden = true;
        $("productListPanel").hidden = true;
      } else {
        const form = $("verificationForm");
        $("verificationInstructions").hidden = false;
        $("verificationPanel").classList.remove("is-loading");
        form.hidden = false;
        $("verificationStatus").innerHTML =
          '<span class="text-muted-soft">Верифікацію ще не подано.</span>';
        $("productListPanel").hidden = true;
      }
    }
  }

  async function initSellerPage() {
    if (sellerInitialized) return;
    sellerInitialized = true;
    debugStats.init += 1;
    if (window.MS_DEBUG === true) {
      console.debug("[seller] init:", debugStats.init);
    }

    bindProductForm();
    bindVerificationForm();
    bindSellerList();

    const user = await window.MSAuth.ready();
    if (!user || !user.authenticated) {
      window.location.href = "/pages/login.html";
      return;
    }

    if (window.MS_DEBUG === true) {
      debugStats.verificationFetch += 1;
      console.debug("[seller] fetch верифікації:", debugStats.verificationFetch);
    }

    const cached = readVerificationCache(user.id);
    if (cached) showVerified(cached.age);
    else showVerificationLoading();

    const walletPromise = (async () => {
      debugStats.walletFetch += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[seller] fetch застави й балансу:", debugStats.walletFetch);
      }
      try {
        await window.MS.get("/wallet");
      } catch (_err) {
        // Balance remains independent; product data can still render.
      }
    })();

    await Promise.all([
      walletPromise,
      loadProducts(),
    ]);

    await loadVerificationState(false, user);
  }

  function cleanupSellerPage() {
    if (productsAbortController) productsAbortController.abort();
    window.clearTimeout(sellerSearchTimer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSellerPage, { once: true });
  } else {
    initSellerPage();
  }
  window.addEventListener("pagehide", () => {
    cleanupSellerPage();
    if (window.MS_DEBUG === true) {
      console.debug("[seller] початкове завантаження", Object.assign({}, debugStats));
    }
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && !sellerInitialized) initSellerPage();
  });
})();
