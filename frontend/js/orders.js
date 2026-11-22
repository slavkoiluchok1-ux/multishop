/* MultiShop — сторінка замовлень: вкладки покупця/продавця та вікно замовлення. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = window.MS.escapeHtml;
  // Ціни показуємо однаково скрізь: до 3 знаків після коми, зайві нулі прибрано.
  const fmt = (value) => (window.MSPrice ? window.MSPrice.formatMCShort(value) : value);
  const orderDialog = $("orderDialog");
  const activationCodesDialog = $("activationCodesDialog");
  const complaintDialog = $("complaintDialog");
  const reviewDialog = $("reviewDialog");
  let complaintOrderId = null;
  let complaintTypeId = null;
  let complaintPicker = null;
  let reviewTarget = null;
  let ordersInitialized = false;
  let ordersLoadSequence = 0;
  let activeOrdersRequest = null;
  let renderedOrdersSignature = "";
  const debugStats = { init: 0, fetch: 0, render: 0 };

  const CONTACT_LABELS = { email: "Email", phone: "Телефон", telegram: "Telegram", other: "Інші" };
  const COMPLAINT_FIELD_LABELS = {
    order_number: "Номер замовлення",
    product: "Товар (назва або посилання)",
    user_or_content: "Користувач або вміст",
    details: "Деталі",
  };

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return isNaN(date.getTime()) ? "—" : date.toLocaleString("uk-UA");
  }

  /* --- Вкладки --- */

  function currentTab() {
    const params = new URLSearchParams(window.location.search);
    return params.get("tab") === "sell" ? "sell" : "buy";
  }

  function applyTab(tab) {
    $("buySection").hidden = tab !== "buy";
    $("sellSection").hidden = tab !== "sell";
    document.querySelectorAll("#orderTabs [data-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === tab);
    });
  }

  function switchTab(tab) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState(null, "", url);
    const active = tab === "buy" ? $("incomingOrders") : $("myOrders");
    const selected = tab === "buy" ? $("myOrders") : $("incomingOrders");
    active.classList.add("is-tab-switching");
    window.setTimeout(() => {
      active.classList.remove("is-tab-switching");
      applyTab(tab);
      if (window.MSMotion) {
        window.MSMotion.discover(selected);
        window.MSMotion.stagger(selected);
      }
    }, 150);
  }

  /* --- Короткий список --- */

  function renderCard(order) {
    const first = order.first_item
      ? '<div class="small text-muted-soft">' + esc(order.first_item.product_name) +
        " × " + esc(order.first_item.quantity) + "</div>"
      : "";
    return '<article class="card-soft is-clickable" data-order-id="' + esc(order.id) +
      '" role="button" tabindex="0"><div class="card-body">' +
      '<div class="d-flex flex-wrap justify-content-between gap-2 align-items-start">' +
      '<div><p class="eyebrow mb-1">Замовлення №' + esc(order.id) + '</p><h3 class="h5 mb-1">' +
      esc(order.status_display) + '</h3><div class="small text-muted-soft">Контрагент: ' +
      esc(order.counterparty) + " · " + esc(fmt(order.total_mc)) + " MC</div>" + first +
      "</div></div></div></article>";
  }

  async function loadOrders() {
    if (activeOrdersRequest) activeOrdersRequest.cancel();
    const sequence = ++ordersLoadSequence;
    const controller = new AbortController();
    activeOrdersRequest = {
      cancel: () => controller.abort(),
    };
    const error = $("ordersError");
    error.hidden = true;
    const cacheKey = window.MSSkeletons.cacheKey("/orders/mine|/orders/incoming");
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    const renderOrders = (data) => {
      if (sequence !== ordersLoadSequence) return false;
      const mine = data.mine.orders || [];
      const incoming = data.incoming.orders || [];
      const signature = JSON.stringify([
        mine.map((order) => [order.id, order.status, order.updated_at, order.total_mc]),
        incoming.map((order) => [order.id, order.status, order.updated_at, order.total_mc]),
      ]);
      if (signature === renderedOrdersSignature) return false;

      const renderOneContainer = (container, orders, empty) => {
        const existing = new Map(
          Array.from(container.querySelectorAll("[data-order-id]")).map((card) => [
            String(card.dataset.orderId),
            card,
          ])
        );
        let nextPosition = container.firstElementChild;
        orders.forEach((order, index) => {
          const id = String(order.id);
          let card = existing.get(id);
          if (card) {
            existing.delete(id);
            const nextSignature = JSON.stringify(order);
            if (card.dataset.orderSignature !== nextSignature) {
              const updated = document.createElement("div");
              updated.innerHTML = renderCard(order);
              card.replaceChildren(...Array.from(updated.firstElementChild.childNodes));
              card.dataset.orderSignature = nextSignature;
            }
          } else {
            const template = document.createElement("template");
            template.innerHTML = renderCard(order);
            card = template.content.firstElementChild;
            card.dataset.orderSignature = JSON.stringify(order);
            card.style.setProperty("--motion-delay", Math.min(index * 55, 600) + "ms");
          }
          if (card !== nextPosition) container.insertBefore(card, nextPosition);
          nextPosition = card.nextElementSibling;
        });
        existing.forEach((oldCard) => oldCard.remove());
        empty.hidden = orders.length > 0;
      };

      renderOneContainer($("myOrders"), mine, $("myOrdersEmpty"));
      renderOneContainer($("incomingOrders"), incoming, $("incomingOrdersEmpty"));
      renderedOrdersSignature = signature;
      debugStats.render += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[orders] render:", debugStats.render);
      }
      if (window.MSMotion) {
        window.MSMotion.discover($("myOrders"));
        window.MSMotion.discover($("incomingOrders"));
        window.MSMotion.stagger($("myOrders"));
        window.MSMotion.stagger($("incomingOrders"));
      }
      return true;
    };
    if (cached !== undefined) renderOrders(cached);
    const activeTab = currentTab();
    const myOrdersLoading = cached === undefined && activeTab === "buy"
      ? window.MSSkeletons.showSkeleton($("myOrders"), "order", 4)
      : null;
    const incomingLoading = cached === undefined && activeTab === "sell"
      ? window.MSSkeletons.showSkeleton($("incomingOrders"), "order", 4)
      : null;
    try {
      debugStats.fetch += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[orders] fetch:", debugStats.fetch);
      }
      const [mine, incoming] = await Promise.all([
        window.MS.get("/orders/mine", { signal: controller.signal }),
        window.MS.get("/orders/incoming", { signal: controller.signal }),
      ]);
      if (sequence !== ordersLoadSequence) return;
      const data = { mine: mine, incoming: incoming };
      window.MSSkeletons.setCachedData(cacheKey, data);
      if (myOrdersLoading) await myOrdersLoading.finish(() => renderOrders(data));
      else if (incomingLoading) await incomingLoading.finish(() => renderOrders(data));
      else renderOrders(data);
      try {
        await window.MS.post("/notifications/read", { section: "orders" });
      } catch (err) {
        window.MS.toast(err.message || "Не вдалося оновити сповіщення.", "danger");
      }
      if (window.MSCounters) window.MSCounters.refresh();
    } catch (err) {
      if (controller.signal.aborted || sequence !== ordersLoadSequence) return;
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити замовлення.");
      const showError = () => window.MSSkeletons.renderRetryError(error, message, loadOrders, true);
      if (myOrdersLoading) await myOrdersLoading.finish(showError);
      else if (incomingLoading) await incomingLoading.finish(showError);
      else showError();
    }
  }

  /* --- Вікно замовлення --- */

  function contactsHtml(contacts) {
    if (!contacts) return '<div class="small text-muted-soft">Не вказано</div>';
    const rows = Object.keys(CONTACT_LABELS)
      .filter((key) => key !== "other" && contacts[key])
      .map((key) =>
        '<div class="order-contact-item"><strong>' + esc(CONTACT_LABELS[key]) +
        '</strong><div class="small">' + esc(contacts[key]) + "</div></div>"
      );
    if (contacts.other) {
      String(contacts.other).split(/\r?\n/).filter(Boolean).forEach((entry) => {
        const separator = entry.indexOf(":");
        const label = separator > 0 ? entry.slice(0, separator).trim() : "Інший контакт";
        const value = separator > 0 ? entry.slice(separator + 1).trim() : entry;
        rows.push(
          '<div class="order-contact-item"><strong>' + esc(label) +
          '</strong><div class="small">' + esc(value) + "</div></div>"
        );
      });
    }
    return rows.length ? rows.join("") : '<div class="small text-muted-soft">Не вказано</div>';
  }

  function itemsHtml(order) {
    return (order.items || []).map((item) => {
      const variant = item.variant_label
        ? '<div class="small text-muted-soft">' + esc(item.variant_label) + "</div>"
        : "";
      const downloads = (item.downloads || []).map((file) =>
        '<a class="btn btn-outline-primary btn-sm me-1 mb-1" href="' + esc(file.url) + '">' +
        esc(file.title) + "</a>"
      ).join("");
      const activationCodes = activationCodesHtml(item.activation_codes);
      const reviewed = (order.my_reviews || []).some((review) => review.product_id === item.product_id);
      const review = order.is_buyer && order.status === "delivered" && !reviewed
        ? '<button class="btn btn-outline-primary btn-sm mt-2" data-action="review" data-order="' +
          esc(order.id) + '" data-product="' + esc(item.product_id) + '">Залишити відгук</button>'
        : "";
      return '<li class="mb-2"><strong>' + esc(item.product_name) + "</strong>" + variant +
        '<div class="small text-muted-soft">' + esc(item.quantity) + " × " + esc(fmt(item.unit_price_mc)) +
        " MC = " + esc(fmt(item.subtotal_mc)) + " MC</div>" +
        (downloads ? '<div class="mt-2">' + downloads + "</div>" : "") +
        activationCodes +
        (review ? "<div>" + review + "</div>" : "") + "</li>";
    }).join("");
  }

  function activationCodesHtml(codes) {
    if (!Array.isArray(codes) || !codes.length) return "";
    return '<div class="activation-code-list"><strong class="small">Коди активації · натисніть, щоб скопіювати</strong>' +
      codes.map((code, index) =>
        '<button class="activation-code-copy" type="button" data-copy-activation="' +
        esc(code) + '" aria-label="Скопіювати код активації ' + (index + 1) + '">' +
        esc(code) + "</button>"
      ).join("") + "</div>";
  }

  async function copyActivationCode(button) {
    try {
      await navigator.clipboard.writeText(button.dataset.copyActivation);
      window.MS.toast("Код скопійовано.", "success");
    } catch (_err) {
      window.MS.toast("Не вдалося скопіювати код. Спробуйте скопіювати його вручну.", "danger");
    }
  }

  async function showCheckoutActivationCodes() {
    const url = new URL(window.location.href);
    const ids = (url.searchParams.get("checkout") || "")
      .split(",").map((id) => id.trim()).filter((id) => /^\d+$/.test(id));
    if (!ids.length) return;
    url.searchParams.delete("checkout");
    window.history.replaceState(null, "", url);
    try {
      const orders = await Promise.all(ids.map((id) => window.MS.get("/orders/" + id)));
      const entries = [];
      orders.forEach((order) => (order.items || []).forEach((item) => {
        if (item.activation_codes && item.activation_codes.length) {
          entries.push('<section class="activation-code-order"><h3 class="h6">Замовлення №' +
            esc(order.id) + " · " + esc(item.product_name) + "</h3>" +
            activationCodesHtml(item.activation_codes) + "</section>");
        }
      }));
      if (!entries.length) return;
      $("activationCodesBody").innerHTML =
        '<p class="text-muted-soft">Ці коди також збережені у відповідних замовленнях. Натисніть код, щоб скопіювати.</p>' +
        entries.join("");
      activationCodesDialog.showModal();
    } catch (err) {
      window.MS.toast(err.message || "Не вдалося завантажити коди покупки.", "danger");
    }
  }

  function historyHtml(order) {
    const rows = order.status_history || [];
    if (!rows.length) return '<div class="small text-muted-soft">Історії немає.</div>';
    return rows.map((row) =>
      '<div class="small">' + esc(formatDate(row.created_at)) + " — " + esc(row.new_status_display) + "</div>"
    ).join("");
  }

  function addressHtml(order) {
    if (!order.delivery_address) {
      return '<div class="small text-muted-soft">Цифровий товар, адреса не потрібна.</div>';
    }
    return '<div class="small">Отримувач: ' + esc(order.recipient_name || "—") + "</div>" +
      '<div class="small">' + esc(order.delivery_address) + "</div>";
  }

  function reviewsHtml(order) {
    const reviews = order.my_reviews || [];
    if (!reviews.length) return "";
    return '<section class="order-info-card"><h3 class="h6 mb-2">Мій коментар</h3>' +
      reviews.map((review) =>
        '<div class="order-comment small mb-2"><strong>' + esc(review.product_name) + "</strong> — " +
        esc(formatDate(review.created_at)) + '<div>' + esc(review.text) + "</div></div>"
      ).join("") + "</section>";
  }

  function cancellationHtml(order) {
    const cancellation = order.cancellation;
    if (!cancellation) return "";
    let text = "Скасування: відкрито скаргу";
    if (cancellation.path === "mutual") {
      const state = {
        pending: cancellation.is_proposer
          ? "Ви запропонували взаємне скасування · очікуємо відповідь"
          : "Вам запропонували взаємне скасування",
        rejected: "Пропозицію взаємного скасування відхилено",
        withdrawn: "Пропозицію взаємного скасування відкликано",
        completed: "Замовлення скасовано за згодою сторін",
      };
      text = state[cancellation.status] || "Взаємне скасування";
    }
    return '<div class="order-info-card small">' + esc(text) + "</div>";
  }

  function actionButtons(order) {
    const buttons = [];
    if (order.can_ship) {
      buttons.push('<button class="btn btn-primary btn-sm" data-action="ship" data-id="' +
        esc(order.id) + '">Позначити відправленим</button>');
    }
    if (order.can_confirm) {
      buttons.push('<button class="btn btn-primary btn-sm" data-action="confirm" data-id="' +
        esc(order.id) + '">Підтвердити отримання</button>');
    }
    const cancellation = order.cancellation;
    if (cancellation && cancellation.path === "mutual" && cancellation.status === "pending") {
      if (cancellation.can_accept) {
        buttons.push('<button class="btn btn-primary btn-sm" data-action="mutual-accept" data-id="' +
          esc(order.id) + '">Прийняти скасування</button>');
        buttons.push('<button class="btn btn-outline-danger btn-sm" data-action="mutual-reject" data-id="' +
          esc(order.id) + '">Відхилити</button>');
      } else if (cancellation.can_withdraw) {
        buttons.push('<button class="btn btn-outline-primary btn-sm" data-action="mutual-withdraw" data-id="' +
          esc(order.id) + '">Забрати пропозицію</button>');
      }
    } else if (order.can_propose_mutual) {
      buttons.push('<button class="btn btn-outline-primary btn-sm" data-action="mutual-propose" data-id="' +
        esc(order.id) + '">Запропонувати взаємне скасування</button>');
    }
    if (order.can_open_complaint) {
      buttons.push('<button class="btn btn-outline-danger btn-sm" data-action="complaint" data-id="' +
        esc(order.id) + '">Скасувати через скаргу</button>');
    }
    // Точка входу в новий розділ скарг (усі види, не лише скасування)
    buttons.push('<button class="btn btn-outline-primary btn-sm" data-action="complain" data-id="' +
      esc(order.id) + '">Поскаржитись</button>');
    return buttons.join("");
  }

  function orderCommentsHtml(order) {
    const comments = (order.comments || []).map((comment) =>
      '<div class="order-comment small mb-2"><strong>' + esc(comment.author) + "</strong> · " +
      esc(formatDate(comment.created_at)) + '<div>' + esc(comment.text) + "</div></div>"
    ).join("");
    const form = order.can_comment
      ? '<label class="form-label small" for="orderCommentText">Написати коментар</label>' +
        '<textarea class="form-control mb-2" id="orderCommentText" rows="2" maxlength="2000"></textarea>' +
        '<button class="btn btn-outline-primary btn-sm" type="button" data-action="comment" data-id="' +
        esc(order.id) + '">Надіслати</button>'
      : "";
    return '<section class="order-info-card"><h3 class="h6 mb-2">Коментарі до замовлення</h3>' +
      (comments || '<p class="small text-muted-soft mb-2">Коментарів ще немає.</p>') +
      form + "</section>";
  }

  function renderDialog(order) {
    $("orderDialogTitle").textContent = "Замовлення №" + order.id;
    $("orderDialogStatus").textContent = order.status_display;
    $("orderDialogBody").innerHTML =
      '<section class="order-info-card"><h3 class="h6 mb-2">Позиції</h3><ul class="mb-0 ps-3">' +
        itemsHtml(order) + "</ul></section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Сума</h3><div class="small">' +
        esc(fmt(order.total_mc)) + " MC</div></section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Дата оплати</h3><div class="small">' +
        esc(formatDate(order.paid_at)) + "</div></section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Контакти покупця</h3>' +
        contactsHtml(order.buyer_contacts) + "</section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Доставка</h3>' +
        addressHtml(order) + "</section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Контрагент</h3><div class="small">' +
        esc(order.counterparty) + "</div></section>" +
      '<section class="order-info-card"><h3 class="h6 mb-2">Історія статусів</h3>' +
        historyHtml(order) + "</section>" +
      cancellationHtml(order) + reviewsHtml(order) + orderCommentsHtml(order);
    $("orderDialogActions").innerHTML = actionButtons(order);
  }

  async function openOrder(orderId) {
    try {
      const order = await window.MS.get("/orders/" + orderId);
      renderDialog(order);
      if (!orderDialog.open) orderDialog.showModal();
    } catch (err) {
      window.MS.toast(err.message || "Не вдалося відкрити замовлення.", "danger");
    }
  }

  function updateOrderCard(order) {
    document.querySelectorAll("[data-order-id]").forEach((card) => {
      if (String(card.dataset.orderId) !== String(order.id)) return;
      const updated = document.createElement("div");
      updated.innerHTML = renderCard(order);
      card.replaceChildren(...Array.from(updated.firstElementChild.childNodes));
      card.dataset.orderSignature = JSON.stringify(order);
      card.classList.add("is-status-updated");
      window.setTimeout(() => card.classList.remove("is-status-updated"), 650);
    });
  }

  async function runAction(action, id) {
    if (action === "ship") {
      await window.MS.request("/orders/" + id + "/status", {
        method: "PATCH",
        body: { status: "shipped" },
      });
    } else if (action === "confirm") {
      await window.MS.post("/orders/" + id + "/confirm-received", {});
    } else if (action.indexOf("mutual-") === 0) {
      await window.MS.post("/orders/" + id + "/cancel-agree", {
        action: action.slice("mutual-".length),
      });
    } else if (action === "comment") {
      const text = $("orderCommentText").value.trim();
      if (!text) {
        throw new Error("Напишіть коментар.");
      }
      await window.MS.post("/orders/" + id + "/comments", { text: text });
      await openOrder(id);
      if (window.MSCounters) window.MSCounters.refresh();
      return;
    }
    if (action === "comment") return;
    try {
      const order = await window.MS.get("/orders/" + id);
      updateOrderCard(order);
      renderDialog(order);
      if (!orderDialog.open) orderDialog.showModal();
    } catch (err) {
      await openOrder(id);
    }
    window.dispatchEvent(new Event("ms:wallet-changed"));
  }

  /* Обов'язкові поля вибраного типу: номер замовлення підставляється сам. */
  function renderComplaintFields(type) {
    const required = (type && type.required_fields) || [];
    $("complaintFields").innerHTML = required.map((name) =>
      '<div class="mb-2"><label class="form-label" for="ocf-' + esc(name) + '">' +
        esc(COMPLAINT_FIELD_LABELS[name] || name) + "</label>" +
      '<input class="form-control" type="text" id="ocf-' + esc(name) +
        '" data-field="' + esc(name) + '" value="' +
        esc(name === "order_number" ? String(complaintOrderId) : "") + '"></div>'
    ).join("");
  }

  function openComplaint(orderId) {
    complaintOrderId = orderId;
    complaintTypeId = null;
    $("complaintError").hidden = true;
    $("complaintText").value = "";
    $("complaintFields").innerHTML = "";
    complaintPicker.setValue(null);
    complaintDialog.showModal();
  }

  /* Об'єкт «Поскаржитись»: спільна форма скарги без переходу зі сторінки */
  function openComplaintForm(orderId) {
    window.MSComplaintForm.open({
      view: "orders",
      target_id: orderId,
      label: "Замовлення №" + orderId,
      context_fields: { order_number: String(orderId) },
    });
  }

  function bindCards(container) {
    const open = (target) => {
      const card = target.closest("[data-order-id]");
      if (card) openOrder(card.dataset.orderId);
    };
    container.addEventListener("click", (event) => open(event.target));
    container.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open(event.target);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (ordersInitialized) return;
    ordersInitialized = true;
    debugStats.init += 1;
    if (window.MS_DEBUG === true) {
      console.debug("[orders] init:", debugStats.init);
    }
    document.querySelectorAll("#orderTabs [data-tab]").forEach((button) => {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
    });
    applyTab(currentTab());
    bindCards($("myOrders"));
    bindCards($("incomingOrders"));

    $("orderDialogClose").addEventListener("click", () => orderDialog.close());
    $("activationCodesClose").addEventListener("click", () => activationCodesDialog.close());
    document.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-copy-activation]");
      if (button) await copyActivationCode(button);
    });
    orderDialog.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) {
        // Клік поза вікном (по backdrop) закриває його.
        if (event.target === orderDialog) orderDialog.close();
        return;
      }
      const action = button.dataset.action;
      if (action === "complain") return openComplaintForm(button.dataset.id);
      if (action === "complaint") return openComplaint(button.dataset.id);
      if (action === "review") {
        reviewTarget = { orderId: button.dataset.order, productId: button.dataset.product };
        $("reviewError").hidden = true;
        $("reviewText").value = "";
        $("reviewMedia").value = "";
        reviewDialog.showModal();
        return;
      }
      button.disabled = true;
      try {
        await runAction(action, button.dataset.id);
      } catch (err) {
        window.MS.toast(err.message || "Дію не виконано.", "danger");
        button.disabled = false;
      }
    });

    // Спільний блок вибору типу скарги (complaint-tree.js)
    complaintPicker = window.MSComplaintTree.renderPicker($("complaintTypePath"), {
      views: ["orders"],
      onChange: (type) => {
        complaintTypeId = type.id;
        renderComplaintFields(type);
      },
    });

    $("complaintClose").addEventListener("click", () => complaintDialog.close());
    $("reviewClose").addEventListener("click", () => reviewDialog.close());
    $("complaintForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const error = $("complaintError");
      error.hidden = true;
      if (!complaintTypeId) {
        complaintPicker.error();
        error.textContent = "Оберіть тип скарги.";
        error.hidden = false;
        return;
      }
      const fields = {};
      const fieldInputs = Array.from($("complaintFields").querySelectorAll("[data-field]"));
      fieldInputs.forEach((input) => { fields[input.getAttribute("data-field")] = input.value.trim(); });
      const emptyField = fieldInputs.find((input) => !input.value.trim());
      if (emptyField) {
        const name = emptyField.getAttribute("data-field");
        error.textContent = "Заповніть поле «" + (COMPLAINT_FIELD_LABELS[name] || name) + "».";
        error.hidden = false;
        emptyField.focus();
        return;
      }
      try {
        await window.MS.post("/orders/" + complaintOrderId + "/cancel", {
          complaint_type_id: complaintTypeId,
          text: $("complaintText").value.trim(),
          fields: fields,
        });
        complaintDialog.close();
        await loadOrders();
        window.dispatchEvent(new Event("ms:wallet-changed"));
      } catch (err) {
        error.textContent = err.message || "Не вдалося створити скаргу.";
        error.hidden = false;
      }
    });
    $("reviewForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const error = $("reviewError");
      error.hidden = true;
      const data = new FormData();
      data.append("order_id", reviewTarget.orderId);
      data.append("text", $("reviewText").value.trim());
      Array.from($("reviewMedia").files).forEach((file) => data.append("media", file));
      try {
        await window.MS.post(
          "/products/" + reviewTarget.productId + "/reviews",
          data
        );
        reviewDialog.close();
        window.MS.toast("Відгук надіслано.", "success");
        if (orderDialog.open) await openOrder(reviewTarget.orderId);
      } catch (err) {
        error.textContent = err.message || "Не вдалося надіслати відгук.";
        error.hidden = false;
      }
    });
    const user = await window.MSAuth.ready();
    if (!user || !user.authenticated) {
      window.location.href = "/pages/login.html";
      return;
    }
    await loadOrders();
    await showCheckoutActivationCodes();
  });
})();
