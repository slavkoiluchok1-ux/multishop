/* MultiShop — complaint-form.js
   Спільна форма «Нова скарга»: сама створює модальне вікно (<dialog>), обирає тип
   деревом (complaint-tree.js) і надсилає POST /api/complaints (FormData).
   Використовує window.MS (api.js) і window.MSComplaintTree.

   Використання:
     window.MSComplaintForm.open({ view, target_id, comment_id, label, onSent });
   Без view — форма відкривається з редагованими полями об'єкта (вкладка «Скарги»). */
(function () {
  "use strict";

  const FIELD_LABELS = {
    order_number: "Номер замовлення",
    product: "Товар (назва або посилання)",
    user_or_content: "Користувач або вміст",
    details: "Деталі",
  };
  const VIEW_LABELS = {
    orders: "На замовлення",
    products: "На товар",
    users_content: "На користувача або вміст",
  };

  const els = {};
  let dialog = null;
  let picker = null;
  let fixed = null; // Контекст об'єкта для автозаповнення полів.
  let onSent = null;

  function esc(value) {
    return window.MS.escapeHtml(value);
  }

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "checkout-dialog complaint-dialog";
    dialog.innerHTML =
      '<form id="msComplaintForm" novalidate>' +
        '<div class="d-flex justify-content-between gap-3 mb-3">' +
          "<div>" +
            '<p class="small text-secondary mb-1">Спори та порушення</p>' +
            '<h2 class="h4 mb-0">Нова скарга</h2>' +
          "</div>" +
          '<button class="btn-close" type="button" id="msComplaintClose" aria-label="Закрити"></button>' +
        "</div>" +
        '<div class="alert-soft is-danger mb-3" id="msComplaintError" hidden></div>' +

        '<section class="card-soft mb-3"><div class="card-body">' +
          '<h3 class="h6 mb-3">Об’єкт скарги</h3>' +
          '<div id="msComplaintViewRow">' +
            '<label class="complaint-form-label" for="msComplaintView">На що подаєте скаргу</label>' +
            '<select class="form-select mb-2" id="msComplaintView">' +
              '<option value="orders">На замовлення</option>' +
              '<option value="products">На товар</option>' +
              '<option value="users_content">На користувача або вміст</option>' +
            "</select>" +
          "</div>" +
          '<div id="msComplaintViewFixed" class="mb-2" hidden>' +
            '<span class="complaint-form-label">На що подаєте скаргу</span>' +
            '<p class="mb-0 fw-bold" id="msComplaintViewText"></p>' +
          "</div>" +
          '<div id="msComplaintTargetField">' +
            '<label class="complaint-form-label" for="msComplaintTarget" id="msComplaintTargetLabel">ID замовлення</label>' +
            '<input class="form-control mb-2" type="text" id="msComplaintTarget" placeholder="Вкажіть ID замовлення">' +
          "</div>" +
          '<div id="msComplaintCommentField" hidden>' +
            '<label class="complaint-form-label" for="msComplaintComment">ID коментаря</label>' +
            '<input class="form-control mb-2" type="text" id="msComplaintComment">' +
          "</div>" +
          '<div id="msComplaintObjectRow" hidden>' +
            '<span class="complaint-form-label">Об’єкт скарги</span>' +
            '<p class="mb-0 fw-bold" id="msComplaintObjectLabel"></p>' +
          "</div>" +
        "</div></section>" +

        '<section class="card-soft mb-3"><div class="card-body">' +
          '<h3 class="h6 mb-3">Причина звернення</h3>' +
          '<label class="complaint-form-label">Тип скарги</label>' +
          '<div id="msComplaintPicker"></div>' +
        "</div></section>" +

        '<section class="card-soft mb-3"><div class="card-body">' +
          '<h3 class="h6 mb-3">Опишіть ситуацію</h3>' +
          '<div id="msComplaintFields" class="mb-2"></div>' +
          '<label class="complaint-form-label mt-2" for="msComplaintText">Що сталося?</label>' +
          '<textarea class="form-control" id="msComplaintText" rows="4" placeholder="Коротко опишіть проблему і бажане рішення"></textarea>' +
        "</div></section>" +

        '<section class="card-soft mb-3"><div class="card-body">' +
          '<h3 class="h6 mb-2">Докази</h3>' +
          '<p class="small text-muted-soft mb-2">Додайте файли, які допоможуть розглянути скаргу. До 100 МБ, необов’язково.</p>' +
          '<input class="form-control" type="file" id="msComplaintEvidence" multiple aria-label="Докази до скарги">' +
        "</div></section>" +

        '<div class="d-flex justify-content-end gap-2">' +
          '<button class="btn btn-outline-primary" type="button" id="msComplaintCancel">Скасувати</button>' +
          '<button class="btn btn-primary" type="submit" id="msComplaintSubmit">Надіслати скаргу</button>' +
        "</div>" +
      "</form>";
    document.body.appendChild(dialog);

    els.error = dialog.querySelector("#msComplaintError");
    els.viewRow = dialog.querySelector("#msComplaintViewRow");
    els.viewFixed = dialog.querySelector("#msComplaintViewFixed");
    els.viewText = dialog.querySelector("#msComplaintViewText");
    els.view = dialog.querySelector("#msComplaintView");
    els.targetField = dialog.querySelector("#msComplaintTargetField");
    els.commentField = dialog.querySelector("#msComplaintCommentField");
    els.objectRow = dialog.querySelector("#msComplaintObjectRow");
    els.objectLabel = dialog.querySelector("#msComplaintObjectLabel");
    els.target = dialog.querySelector("#msComplaintTarget");
    els.targetLabel = dialog.querySelector("#msComplaintTargetLabel");
    els.comment = dialog.querySelector("#msComplaintComment");
    els.fields = dialog.querySelector("#msComplaintFields");
    els.text = dialog.querySelector("#msComplaintText");
    els.evidence = dialog.querySelector("#msComplaintEvidence");
    els.submit = dialog.querySelector("#msComplaintSubmit");

    picker = window.MSComplaintTree.renderPicker(dialog.querySelector("#msComplaintPicker"), {
      onChange: (type) => renderFields(type ? type.required_fields : []),
    });

    dialog.querySelector("#msComplaintClose").addEventListener("click", () => dialog.close());
    dialog.querySelector("#msComplaintCancel").addEventListener("click", () => dialog.close());
    dialog.querySelector("#msComplaintForm").addEventListener("submit", submit);
    els.view.addEventListener("change", () => {
      els.commentField.hidden = els.view.value !== "users_content";
      picker.setViews([els.view.value]);
      const labels = {
        orders: ["ID замовлення", "Вкажіть ID замовлення"],
        products: ["ID товару", "Вкажіть ID товару"],
        users_content: ["ID користувача", "Вкажіть ID користувача"],
      };
      els.targetLabel.textContent = labels[els.view.value][0];
      els.target.placeholder = labels[els.view.value][1];
    });
  }

  function contextFieldValue(name) {
    if (!fixed) return "";
    const contextFields = fixed.context_fields || {};
    if (Object.prototype.hasOwnProperty.call(contextFields, name)) {
      return String(contextFields[name] || "");
    }
    if (name === "order_number" && fixed.view === "orders") {
      return fixed.target_id;
    }
    if (name === "product" && fixed.view === "products") {
      return (fixed.label || defaultLabel(fixed)).replace(/^Товар:\s*/, "") +
        " (ID: " + fixed.target_id + ")";
    }
    if (name === "user_or_content" && fixed.view === "users_content") {
      return (fixed.label || defaultLabel(fixed)) + " (ID: " + fixed.target_id + ")";
    }
    return "";
  }

  function renderFields(required) {
    els.fields.innerHTML = (required || []).map((name) =>
      '<div class="mb-2"><label class="complaint-form-label" for="mscf-' + esc(name) + '">' +
        esc(FIELD_LABELS[name] || name) + "</label>" +
      '<input class="form-control" type="text" required id="mscf-' + esc(name) +
        '" data-field="' + esc(name) + '" value="' + esc(contextFieldValue(name)) + '"></div>'
    ).join("");
  }

  function defaultLabel(target) {
    if (target.view === "orders") return "Замовлення №" + target.target_id;
    if (target.view === "products") return "Товар #" + target.target_id;
    if (target.comment_id) return "Коментар #" + target.comment_id;
    return "Користувач #" + target.target_id;
  }

  async function submit(event) {
    event.preventDefault();
    els.error.hidden = true;

    const type = picker.getValue();
    if (!type) {
      picker.error();
      els.error.textContent = "Оберіть тип скарги.";
      els.error.hidden = false;
      return;
    }
    const text = els.text.value.trim();
    if (!text) {
      els.error.textContent = "Опишіть проблему.";
      els.error.hidden = false;
      els.text.focus();
      return;
    }

    const view = fixed ? fixed.view : els.view.value;
    const target = fixed ? fixed.target_id : els.target.value.trim();
    const commentId = fixed ? fixed.comment_id : els.comment.value.trim();
    if (!target && !commentId) {
      els.error.textContent = "Вкажіть ID об’єкта, на який подаєте скаргу.";
      els.error.hidden = false;
      if (!fixed) els.target.focus();
      return;
    }

    const fields = {};
    els.fields.querySelectorAll("[data-field]").forEach((input) => {
      fields[input.getAttribute("data-field")] = input.value.trim();
    });
    const missingField = (type.required_fields || []).find((name) => !fields[name]);
    if (missingField) {
      const input = Array.from(els.fields.querySelectorAll("[data-field]")).find(
        (item) => item.getAttribute("data-field") === missingField
      );
      els.error.textContent = "Заповніть поле «" + (FIELD_LABELS[missingField] || missingField) + "».";
      els.error.hidden = false;
      if (input) input.focus();
      return;
    }

    const form = new FormData();
    form.append("complaint_type_id", type.id);
    form.append("text", text);
    form.append("fields", JSON.stringify(fields));
    if (view === "orders") form.append("order_id", target);
    else if (view === "products") form.append("product_id", target);
    else if (commentId) form.append("comment_id", commentId);
    else form.append("target_user_id", target);
    Array.from(els.evidence.files).forEach((file) => form.append("evidence", file));

    els.submit.disabled = true;
    try {
      await window.MS.post("/complaints", form);
      dialog.close();
      window.MS.toast("Скаргу надіслано.", "success");
      if (typeof onSent === "function") onSent();
    } catch (err) {
      els.error.textContent = err.message || "Не вдалося надіслати скаргу.";
      els.error.hidden = false;
      els.submit.disabled = false;
    }
  }

  /* options: { view, target_id, comment_id, label, onSent } */
  function open(options) {
    options = options || {};
    if (!dialog) build();

    els.error.hidden = true;
    els.error.textContent = "";
    els.fields.innerHTML = "";
    els.text.value = "";
    els.evidence.value = "";
    els.target.value = "";
    els.comment.value = "";
    els.submit.disabled = false;
    onSent = options.onSent || null;

    fixed = options.view
      ? {
          view: options.view,
          target_id: options.target_id != null ? String(options.target_id) : "",
          comment_id: options.comment_id != null ? String(options.comment_id) : "",
          label: options.label || "",
          context_fields: options.context_fields || {},
        }
      : null;

    if (fixed) {
      // Об'єкт заданий: вид і ціль фіксовані, показуємо прочитаний підпис.
      els.viewRow.hidden = true;
      els.viewFixed.hidden = false;
      els.viewText.textContent = VIEW_LABELS[fixed.view] || fixed.view;
      els.targetField.hidden = true;
      els.commentField.hidden = true;
      els.objectRow.hidden = false;
      els.objectLabel.textContent = options.label || defaultLabel(fixed);
      picker.setViews([fixed.view]);
      // Звужуємо гілки під об'єкт: скарга на коментар — лише коментарі,
      // скарга на користувача — усе, крім коментарів.
      picker.setScope(
        fixed.view === "users_content" ? (fixed.comment_id ? "comment" : "user") : null
      );
    } else {
      // Без об'єкта (вкладка «Скарги»): вид і поля об'єкта редаговані.
      els.viewRow.hidden = false;
      els.viewFixed.hidden = true;
      els.objectRow.hidden = true;
      els.targetField.hidden = false;
      els.view.value = "orders";
      els.commentField.hidden = true;
      els.targetLabel.textContent = "ID замовлення";
      els.target.placeholder = "Вкажіть ID замовлення";
      picker.setViews([els.view.value]);
      picker.setScope(null);
    }

    picker.setValue(null);
    if (!dialog.open) dialog.showModal();
  }

  window.MSComplaintForm = { open: open };
})();
