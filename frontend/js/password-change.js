/* MultiShop — password-change.js
   Спливаюче вікно зміни паролю.

   Два режими:
   - forced=true:  тимчасовий пароль від підтримки. Лише новий пароль двічі,
                   без кнопки закриття і без старого паролю. Закрити не можна,
                   поки не збережено.
   - forced=false: звичайна зміна з профілю: старий + новий + підтвердження,
                   є кнопка закриття.

   Використовує window.MS (api.js) і window.MSAuth (auth.js). */
(function () {
  "use strict";

  const MODAL_ID = "msPasswordChangeModal";
  let currentForced = false;
  let currentOnSuccess = null;

  function buildModal() {
    const modal = document.createElement("div");
    modal.className = "app-modal";
    modal.id = MODAL_ID;
    modal.hidden = true;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.innerHTML =
      '<div class="app-modal-box">' +
        '<button class="app-modal-close" type="button" data-pwd-close aria-label="Закрити">×</button>' +
        '<p class="eyebrow mb-1">Безпека акаунта</p>' +
        '<h2 class="h5 mb-3" data-pwd-title>Зміна паролю</h2>' +
        '<div class="alert-soft is-danger mb-3" data-pwd-error hidden></div>' +
        '<div class="alert-soft is-success mb-3" data-pwd-success hidden></div>' +
        '<form data-pwd-form novalidate>' +
          '<div class="mb-3" data-pwd-old-wrap>' +
            '<label class="form-label" for="pwdOld">Поточний пароль</label>' +
            '<input class="form-control" type="password" id="pwdOld" autocomplete="current-password">' +
          "</div>" +
          '<div class="mb-3">' +
            '<label class="form-label" for="pwdNew">Новий пароль</label>' +
            '<input class="form-control" type="password" id="pwdNew" autocomplete="new-password" required>' +
          "</div>" +
          '<div class="mb-3">' +
            '<label class="form-label" for="pwdNew2">Підтвердіть новий пароль</label>' +
            '<input class="form-control" type="password" id="pwdNew2" autocomplete="new-password" required>' +
          "</div>" +
          '<button class="btn btn-primary w-100" type="submit" data-pwd-submit>Зберегти</button>' +
        "</form>" +
      "</div>";
    document.body.appendChild(modal);
    return modal;
  }

  function getModal() {
    return document.getElementById(MODAL_ID) || buildModal();
  }

  function showError(modal, message) {
    const el = modal.querySelector("[data-pwd-error]");
    el.textContent = message || "";
    el.hidden = !message;
    modal.querySelector("[data-pwd-success]").hidden = true;
  }

  function showSuccess(modal, message) {
    const el = modal.querySelector("[data-pwd-success]");
    el.textContent = message || "";
    el.hidden = !message;
    modal.querySelector("[data-pwd-error]").hidden = true;
  }

  async function submit(modal) {
    const oldInput = modal.querySelector("#pwdOld");
    const newInput = modal.querySelector("#pwdNew");
    const new2Input = modal.querySelector("#pwdNew2");
    const submitBtn = modal.querySelector("[data-pwd-submit]");

    const newPassword = newInput.value;
    const newPassword2 = new2Input.value;
    if (!newPassword) {
      showError(modal, "Введіть новий пароль.");
      return;
    }
    if (newPassword !== newPassword2) {
      showError(modal, "Паролі не збігаються.");
      return;
    }

    const payload = { new_password: newPassword, new_password2: newPassword2 };
    if (!currentForced) {
      if (!oldInput.value) {
        showError(modal, "Введіть поточний пароль.");
        return;
      }
      payload.old_password = oldInput.value;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Зберігаємо…";
    try {
      await window.MS.post("/profile/password", payload);
      showSuccess(modal, "Пароль оновлено.");
      if (window.MSAuth) await window.MSAuth.refresh();
      if (typeof currentOnSuccess === "function") {
        setTimeout(() => currentOnSuccess(), 400);
      }
      setTimeout(() => close(modal), 600);
    } catch (err) {
      const payload = err && err.payload ? err.payload : {};
      const fields = payload.fields || payload;
      let message = "";
      if (fields.old_password) message = firstMsg(fields.old_password);
      else if (fields.new_password) message = firstMsg(fields.new_password);
      else if (fields.new_password2) message = firstMsg(fields.new_password2);
      showError(modal, message || (err && err.message) || "Не вдалося змінити пароль.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Зберегти";
    }
  }

  function firstMsg(value) {
    if (Array.isArray(value)) return value.length ? String(value[0]) : "";
    return value ? String(value) : "";
  }

  function close(modal) {
    modal.hidden = true;
    currentOnSuccess = null;
  }

  function open(options) {
    const opts = options || {};
    currentForced = !!opts.forced;
    currentOnSuccess = typeof opts.onSuccess === "function" ? opts.onSuccess : null;

    const modal = getModal();
    const title = modal.querySelector("[data-pwd-title]");
    const closeBtn = modal.querySelector("[data-pwd-close]");
    const oldWrap = modal.querySelector("[data-pwd-old-wrap]");
    const form = modal.querySelector("[data-pwd-form]");

    title.textContent = currentForced
      ? "Змініть тимчасовий пароль"
      : "Зміна паролю";
    // Примусовий режим: без старого паролю і без можливості закрити.
    oldWrap.hidden = currentForced;
    closeBtn.style.display = currentForced ? "none" : "";

    form.reset();
    showError(modal, "");
    showSuccess(modal, "");
    modal.hidden = false;

    const focusTarget = currentForced ? modal.querySelector("#pwdNew") : modal.querySelector("#pwdOld");
    if (focusTarget) setTimeout(() => focusTarget.focus(), 50);
  }

  function bindOnce() {
    const modal = getModal();
    const form = modal.querySelector("[data-pwd-form]");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit(modal);
    });
    // Кнопка «Змінити пароль» у профілі: звичайний режим (старий + новий двічі).
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-change-password]");
      if (!trigger) return;
      event.preventDefault();
      open({ forced: false });
    });
    modal.querySelector("[data-pwd-close]").addEventListener("click", () => {
      if (!currentForced) close(modal);
    });
    // Клік поза вікном: у примусовому режимі не закриваємо.
    modal.addEventListener("click", (event) => {
      if (event.target === modal && !currentForced) close(modal);
    });
    // Esc: у примусовому режимі ігноруємо.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !currentForced && !modal.hidden) close(modal);
    });
  }

  document.addEventListener("DOMContentLoaded", bindOnce);

  window.MSPasswordChange = {
    open: open,
  };
})();
