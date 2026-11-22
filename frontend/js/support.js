/* MultiShop — support.js
   Сторінка «Підтримка» для людини й гостя.
   Гість (без входу) подає звернення через POST /api/support/guest, вказавши email.
   Залогінений бачить список своїх звернень (GET /api/support/tickets), відкриває
   листування й дописує в нього (POST /api/support/tickets/{id}/messages, FormData).
   Використовує window.MS (api.js) і window.MSAuth (auth.js). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = window.MS.escapeHtml;

  // Кольори з components.css: is-gold / is-lime / is-muted.
  const STATUS_BADGES = { open: "is-gold", answered: "is-lime", closed: "is-muted" };
  const CONTACT_FIELDS = [
    ["formPhone", "phone", "contact_phone"],
    ["formTelegram", "telegram", "contact_telegram"],
    ["formInstagram", "instagram", "contact_instagram"],
    ["formViber", "viber", "contact_viber"],
    ["formFacebook", "facebook", "contact_facebook"],
    ["formWhatsApp", "whatsapp", "contact_whatsapp"],
  ];

  let authed = false;
  let currentId = null;
  let requestType = "";
  let accountCheckTimer = null;
  let accountCheckSequence = 0;
  let profileContactValues = null;
  let supportInitialized = false;
  let ticketLoadSequence = 0;
  let activeTicketRequest = null;
  let renderedTicketsSignature = "";
  const debugStats = { init: 0, fetch: 0, render: 0 };
  const pendingBanId = Number(new URLSearchParams(window.location.search).get("ban_id")) || null;

  function contactsHtml(contacts) {
    if (!contacts || !contacts.length) return "";
    return contacts.map((contact) => {
      const value = contact.url
        ? '<a href="' + esc(contact.url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(contact.value) + "</a>"
        : esc(contact.value);
      return (contact.label ? esc(contact.label) + " " : "") + value;
    }).join(" · ");
  }

  function setAccountHint(message, state) {
    const input = $("formAccount");
    const hint = $("formAccountHint");
    input.classList.remove("is-valid", "is-invalid");
    hint.textContent = message;
    hint.className = "small mb-2";
    hint.style.color = "";
    if (state === "valid") input.classList.add("is-valid");
    if (state === "invalid") input.classList.add("is-invalid");
    if (state === "checking" || state === "idle" || state === "error") {
      hint.className += " text-muted-soft";
    }
    if (state === "valid") hint.style.color = "var(--lime, #2e7d32)";
    if (state === "invalid") hint.style.color = "var(--blood, #c62828)";
  }

  async function verifyAccountUsername(value, sequence) {
    try {
      const data = await window.MS.get("/auth/username-check?username=" + encodeURIComponent(value));
      if (sequence !== accountCheckSequence || $("formAccount").value.trim() !== value) return null;
      const exists = !!(data && data.exists);
      setAccountHint(exists ? "Нік знайдено." : "Нік не знайдено.", exists ? "valid" : "invalid");
      return exists;
    } catch {
      if (sequence === accountCheckSequence) {
        setAccountHint("Не вдалося перевірити нік. Спробуйте ще раз.", "error");
      }
      return null;
    }
  }

  function scheduleAccountUsernameCheck() {
    const input = $("formAccount");
    const value = input.value.trim();
    const sequence = ++accountCheckSequence;
    if (accountCheckTimer) clearTimeout(accountCheckTimer);
    if (!value) {
      setAccountHint("Введіть нік для перевірки.", "idle");
      return;
    }
    setAccountHint("Перевірка існування ніку…", "checking");
    accountCheckTimer = setTimeout(() => {
      accountCheckTimer = null;
      verifyAccountUsername(value, sequence);
    }, 400);
  }

  async function validateAccountUsername() {
    const value = $("formAccount").value.trim();
    if (accountCheckTimer) {
      clearTimeout(accountCheckTimer);
      accountCheckTimer = null;
    }
    const sequence = ++accountCheckSequence;
    if (!value) {
      setAccountHint("Введіть нік для перевірки.", "invalid");
      return false;
    }
    setAccountHint("Перевірка існування ніку…", "checking");
    return verifyAccountUsername(value, sequence);
  }

  function setError(id, message) {
    const el = $(id);
    el.textContent = message || "";
    el.hidden = !message;
  }

  function fmtDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return isNaN(date.getTime()) ? "—" : date.toLocaleString("uk-UA");
  }

  function badge(status, label) {
    return '<span class="badge-soft ' + (STATUS_BADGES[status] || "is-muted") + '">' +
      esc(label) + "</span>";
  }

  function messageTextHtml(text) {
    return esc(text || "").replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );
  }

  /* ---------- Список своїх звернень ---------- */

  function renderTickets(rows) {
    const signature = JSON.stringify(rows.map((row) =>
      [row.id, row.status, row.updated_at, row.preview, row.subject]
    ));
    if (signature === renderedTicketsSignature) return false;
    const container = $("ticketList");
    const existing = new Map(
      Array.from(container.querySelectorAll("[data-ticket]")).map((card) => [
        String(card.dataset.ticket),
        card,
      ])
    );
    let nextPosition = container.firstElementChild;
    rows.forEach((row, index) => {
      const id = String(row.id);
      let card = existing.get(id);
      const html =
        '<article class="card-soft is-clickable" data-ticket="' + row.id + '">' +
          '<div class="card-body">' +
            '<div class="d-flex flex-wrap justify-content-between gap-2">' +
              "<div>" +
                '<span class="badge-soft is-muted mb-1">' + esc(row.request_type_display || "Інше") + "</span>" +
                '<h3 class="h6 mb-1">' + esc(row.subject) + "</h3>" +
                (row.contacts && row.contacts.length
                  ? '<div class="small text-muted-soft mb-1">Контакти: ' + contactsHtml(row.contacts) + "</div>"
                  : "") +
                '<div class="small text-muted-soft">' + esc(row.preview || "") + "</div>" +
              "</div>" +
              '<div class="text-end">' + badge(row.status, row.status_display) +
                '<div class="small text-muted-soft mt-1">' + esc(fmtDate(row.created_at)) + "</div>" +
              "</div>" +
            "</div>" +
          "</div>" +
        "</article>";
      if (card) {
        existing.delete(id);
        if (card.dataset.ticketSignature !== JSON.stringify(row)) {
          const template = document.createElement("template");
          template.innerHTML = html;
          card.replaceChildren(...Array.from(template.content.firstElementChild.childNodes));
          card.dataset.ticketSignature = JSON.stringify(row);
        }
      } else {
        const template = document.createElement("template");
        template.innerHTML = html;
        card = template.content.firstElementChild;
        card.dataset.ticketSignature = JSON.stringify(row);
        card.style.setProperty("--motion-delay", Math.min(index * 55, 600) + "ms");
      }
      if (card !== nextPosition) container.insertBefore(card, nextPosition);
      nextPosition = card.nextElementSibling;
    });
    existing.forEach((card) => card.remove());
    $("ticketEmpty").hidden = rows.length > 0;
    renderedTicketsSignature = signature;
    debugStats.render += 1;
    if (window.MS_DEBUG === true) {
      console.debug("[support] render:", debugStats.render);
    }
    if (window.MSMotion) {
      window.MSMotion.discover(container);
      window.MSMotion.stagger(container);
    }
    return true;
  }

  async function loadTickets() {
    if (activeTicketRequest) activeTicketRequest.cancel();
    const sequence = ++ticketLoadSequence;
    const controller = new AbortController();
    activeTicketRequest = { cancel: () => controller.abort() };
    setError("supportError", "");
    const cacheKey = window.MSSkeletons.cacheKey("/support/tickets");
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (cached !== undefined) renderTickets(cached.results || []);
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton($("ticketList"), "support", 4)
      : null;
    try {
      debugStats.fetch += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[support] fetch:", debugStats.fetch);
      }
      const data = await window.MS.get("/support/tickets", { signal: controller.signal });
      if (sequence !== ticketLoadSequence) return;
      window.MSSkeletons.setCachedData(cacheKey, data);
      if (loading) await loading.finish(() => renderTickets(data.results || []));
      else renderTickets(data.results || []);
      // Відповідь підтримки приходить сповіщенням у секцію «support».
      try {
        await window.MS.post("/notifications/read", { section: "support" });
      } catch (err) {
        window.MS.toast(err.message || "Не вдалося оновити сповіщення.", "danger");
      }
      if (window.MSCounters) window.MSCounters.refresh();
    } catch (err) {
      if (controller.signal.aborted || sequence !== ticketLoadSequence) return;
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити звернення.");
      if (loading) {
        await loading.finish(() => {
          window.MSSkeletons.renderRetryError($("ticketList"), message, loadTickets);
        });
      } else {
        window.MSSkeletons.renderRetryError($("ticketList"), message, loadTickets, true);
      }
    }
  }

  /* ---------- Листування у зверненні ---------- */

  function messagesHtml(rows) {
    if (!rows || !rows.length) return '<p class="text-muted-soft small" id="supportMessages">Повідомлень немає.</p>';
    return rows.map((row) =>
      '<div class="support-message small mb-2" data-support-message="' + esc(row.id) +
        '"><strong>' + esc(row.author) + "</strong> · " +
        esc(fmtDate(row.created_at)) +
        (row.attachment
          ? ' · <a href="' + esc(row.attachment) + '" target="_blank" rel="noopener">вкладення</a>'
          : "") +
        "<div>" + messageTextHtml(row.text) + "</div></div>"
    ).join("");
  }

  function renderDetail(data) {
    $("ticketEyebrow").textContent = data.guest ? "Звернення гостя" : "Звернення";
    $("ticketTitle").textContent = data.subject;
    $("ticketBody").innerHTML =
      '<div class="small text-muted-soft mb-2">' + badge(data.status, data.status_display) +
        " · Створено: " + esc(fmtDate(data.created_at)) + "</div>" +
      '<div class="card-soft mb-3"><div class="card-body"><strong>Тип звернення:</strong> ' +
        esc(data.request_type_display || "Інше") + "</div></div>" +
      (data.order_reference
        ? '<div class="card-soft mb-3"><div class="card-body"><strong>Номер замовлення:</strong> ' +
          esc(data.order_reference) + "</div></div>"
        : "") +
      (data.desired_outcome
        ? '<div class="card-soft mb-3"><div class="card-body"><strong>Бажаний результат</strong><p class="mb-0 mt-1">' +
          esc(data.desired_outcome) + "</p></div></div>"
        : "") +
      (data.contacts && data.contacts.length
        ? '<div class="card-soft mb-3"><div class="card-body">' +
          '<strong class="small text-muted-soft d-block mb-1">Контакти</strong>' +
          '<div class="small">' + contactsHtml(data.contacts) + "</div></div></div>"
        : "") +
      '<h3 class="h6 mb-2">Листування</h3><div id="supportMessages">' +
        messagesHtml(data.messages) + "</div>" +
      (data.appeal_of
        ? '<div class="small text-muted-soft mt-2">Апеляція до звернення №' +
          esc(data.appeal_of) + "</div>"
        : "") +
      (data.appeal_rejections && data.appeal_rejections.length
        ? '<div class="card-soft mt-3"><div class="card-body"><strong>Рішення про бан можна оскаржити</strong>' +
          data.appeal_rejections.map((row) =>
            '<div class="mt-2"><label class="form-label" for="appealText' + esc(row.id) +
            '">Текст апеляції</label><textarea class="form-control mb-2" rows="3" id="appealText' +
            esc(row.id) + '"></textarea><button class="btn btn-primary btn-sm" type="button" data-appeal="' +
            esc(row.id) + '">Подати апеляцію</button></div>'
          ).join("") + "</div></div>"
        : "");
    $("replyBox").hidden = !data.can_write;
    $("replyText").value = "";
    $("replyFile").value = "";
    window.requestAnimationFrame(() => {
      const messages = $("supportMessages");
      if (messages) {
        const body = $("ticketBody");
        body.scrollTop = body.scrollHeight;
      }
    });
  }

  async function openTicket(id) {
    currentId = id;
    setError("ticketError", "");
    $("ticketModal").hidden = false;
    $("replyBox").hidden = true;
    const body = $("ticketBody");
    const cacheKey = window.MSSkeletons.cacheKey("/support/tickets/" + id);
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (cached !== undefined) renderDetail(cached);
    else body.replaceChildren();
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton(body, "support", 2)
      : null;
    try {
      const data = await window.MS.get("/support/tickets/" + id);
      window.MSSkeletons.setCachedData(cacheKey, data);
      if (loading) await loading.finish(() => renderDetail(data));
      else renderDetail(data);
    } catch (err) {
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося відкрити звернення.");
      if (loading) {
        await loading.finish(() => {
          body.replaceChildren();
          window.MSSkeletons.renderRetryError(
            $("ticketError"),
            message,
            () => openTicket(id)
          );
        });
      } else {
        window.MSSkeletons.renderRetryError(
          $("ticketError"),
          message,
          () => openTicket(id)
        );
      }
    }
  }

  async function sendReply() {
    const text = $("replyText").value.trim();
    const file = $("replyFile").files[0];
    if (!text && !file) {
      window.MS.toast("Напишіть повідомлення.", "danger");
      return;
    }

    const form = new FormData();
    form.append("text", text);
    if (file) form.append("attachment", file);
    const optimistic = document.createElement("div");
    optimistic.className = "support-message support-message-pending is-new";
    optimistic.innerHTML =
      "<strong>Ви</strong> · відправляється…<div>" +
      esc(text || "Вкладення") +
      '<span class="support-message-spinner" aria-label="Відправляється"></span></div>';
    const messages = $("supportMessages");
    if (messages) {
      const empty = messages.querySelector(".text-muted-soft");
      if (empty) empty.remove();
      messages.appendChild(optimistic);
      $("ticketBody").scrollTop = $("ticketBody").scrollHeight;
    }
    $("replySend").disabled = true;
    try {
      const data = await window.MS.post("/support/tickets/" + currentId + "/messages", form);
      window.MSSkeletons.setCachedData(
        window.MSSkeletons.cacheKey("/support/tickets/" + currentId),
        data
      );
      renderDetail(data);
      await loadTickets();
    } catch (err) {
      optimistic.classList.remove("support-message-pending");
      optimistic.classList.add("support-message-error");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn btn-outline-danger btn-sm mt-2";
      retry.textContent = "Повторити";
      retry.addEventListener("click", () => sendReply());
      optimistic.appendChild(retry);
      setError("ticketError", err.message || "Не вдалося надіслати повідомлення.");
    } finally {
      $("replySend").disabled = false;
      $("replyText").focus();
    }
  }

  async function submitAppeal(rejectionId, text) {
    if (!text.trim()) {
      setError("ticketError", "Опишіть причину апеляції.");
      return;
    }
    try {
      const appeal = await window.MS.post("/support/tickets/" + currentId + "/appeals", {
        rejection_id: rejectionId,
        text: text.trim(),
      });
      setError("ticketError", "");
      window.MS.toast("Апеляцію подано. Її розглянуть адміни 1-го рівня.", "success");
      await loadTickets();
      openTicket(appeal.id);
    } catch (err) {
      setError("ticketError", err.message || "Не вдалося подати апеляцію.");
    }
  }

  /* ---------- Форма нового звернення ---------- */

  function openForm() {
    setError("formError", "");
    $("ticketForm").reset();
    accountCheckSequence += 1;
    if (accountCheckTimer) {
      clearTimeout(accountCheckTimer);
      accountCheckTimer = null;
    }
    requestType = "";
    $("requestTypeBtn").textContent = "Оберіть тип звернення";
    $("emailField").hidden = authed; // залогіненому email не потрібен (беремо з профілю)
    // Поле ніка показуємо лише для типу «Проблема з акаунтом».
    $("accountField").hidden = true;
    $("orderReferenceField").hidden = true;
    $("formAccount").value = "";
    setAccountHint("Введіть нік для перевірки.", "idle");
    $("formModal").hidden = false;
    if (authed) {
      if (profileContactValues) {
        fillContactFields(profileContactValues);
      } else {
        window.MS.get("/profile").then((data) => {
          profileContactValues = (data && data.profile) || {};
          if (!$("formModal").hidden) fillContactFields(profileContactValues);
        }).catch((err) => {
          window.MS.toast(err.message || "Не вдалося завантажити контакти з профілю.", "danger");
        });
      }
    }
  }

  function openBanRequest(user) {
    const ban = user && user.active_ban;
    if (!pendingBanId || !ban || Number(ban.id) !== pendingBanId || !user.is_banned) return;
    openForm();
    requestType = "unban";
    $("requestTypeBtn").textContent = "Розбан або оскарження блокування";
    $("formSubject").value = "Оскарження блокування акаунта";
    $("formText").value =
      "Прошу переглянути блокування акаунта.\n\nПричина, зазначена адміністратором:\n" +
      (ban.reason || "Причину не вказано.");
    $("formOutcome").value = "Прошу перевірити причину блокування та повідомити про рішення.";
  }

  function fillContactFields(profile) {
    CONTACT_FIELDS.forEach(([id, profileKey]) => {
      const input = $(id);
      if (!input.value.trim()) input.value = profile[profileKey] || "";
    });
  }

  async function submitForm(event) {
    event.preventDefault();
    setError("formError", "");
    const subject = $("formSubject").value.trim();
    const text = $("formText").value.trim();
    const desiredOutcome = $("formOutcome").value.trim();
    const email = $("formEmail").value.trim();
    if (!subject) {
      setError("formError", "Укажіть тему звернення.");
      return;
    }
    if (!requestType) {
      setError("formError", "Оберіть тип звернення.");
      $("requestTypeBtn").focus();
      return;
    }
    if (!text) {
      setError("formError", "Опишіть питання.");
      return;
    }
    if (!desiredOutcome) {
      setError("formError", "Напишіть, якого результату ви очікуєте.");
      $("formOutcome").focus();
      return;
    }
    if (!authed && !email) {
      setError("formError", "Укажіть email для зв'язку.");
      return;
    }
    const targetUsername = $("formAccount").value.trim();
    if (requestType === "account") {
      $("formSubmit").disabled = true;
      const usernameExists = await validateAccountUsername();
      if (usernameExists !== true) {
        setError("formError", usernameExists === false
          ? "Акаунт із таким ніком не знайдено."
          : "Не вдалося перевірити нік. Спробуйте ще раз.");
        $("formAccount").focus();
        $("formSubmit").disabled = false;
        return;
      }
    }

    const form = new FormData();
    form.append("subject", subject);
    form.append("request_type", requestType);
    form.append("text", text);
    form.append("desired_outcome", desiredOutcome);
    form.append("contact_email", email);
    CONTACT_FIELDS.forEach(([id, , requestKey]) => {
      form.append(requestKey, $(id).value.trim());
    });
    if (requestType === "account") form.append("target_username", targetUsername);
    if (requestType === "order") {
      form.append("order_reference", $("formOrderReference").value.trim());
    }
    if (requestType === "unban" && pendingBanId) {
      form.append("appeal_ban_id", String(pendingBanId));
    }
    const file = $("formFile").files[0];
    if (file) form.append("attachment", file);

    $("formSubmit").disabled = true;
    try {
      await window.MS.post(authed ? "/support/tickets" : "/support/guest", form);
      $("formModal").hidden = true;
      if (authed) {
        window.MS.toast("Звернення створено.", "success");
        await loadTickets();
      } else {
        window.MS.toast("Звернення надіслано. Відповімо на вашу пошту.", "success");
      }
    } catch (err) {
      setError("formError", err.message || "Не вдалося надіслати звернення.");
    } finally {
      $("formSubmit").disabled = false;
    }
  }

  /* ---------- Події ---------- */

  document.addEventListener("DOMContentLoaded", () => {
    if (supportInitialized) return;
    supportInitialized = true;
    debugStats.init += 1;
    if (window.MS_DEBUG === true) {
      console.debug("[support] init:", debugStats.init);
    }
    $("newTicketBtn").addEventListener("click", openForm);
    $("guestNewBtn").addEventListener("click", (event) => {
      event.preventDefault();
      openForm();
    });
    $("formClose").addEventListener("click", () => { $("formModal").hidden = true; });
    $("formCancel").addEventListener("click", () => { $("formModal").hidden = true; });
    $("ticketForm").addEventListener("submit", submitForm);
    $("requestTypeBtn").addEventListener("click", () => { $("requestTypeModal").hidden = false; });
    $("requestTypeClose").addEventListener("click", () => { $("requestTypeModal").hidden = true; });
    $("requestTypeModal").addEventListener("click", (event) => {
      if (event.target === $("requestTypeModal")) $("requestTypeModal").hidden = true;
      const option = event.target.closest("[data-support-type]");
      if (!option) return;
      requestType = option.getAttribute("data-support-type");
      $("requestTypeBtn").textContent = option.querySelector("strong").textContent;
      $("requestTypeModal").hidden = true;
      // Поле ніка — лише для типу «Проблема з акаунтом».
      const isAccount = requestType === "account";
      $("accountField").hidden = !isAccount;
      $("orderReferenceField").hidden = requestType !== "order";
      if (isAccount) {
        const input = $("formAccount");
        if (!input.value.trim() && authed && window.MSAuth.state && window.MSAuth.state.user) {
          input.value = window.MSAuth.state.user.username || "";
        }
        scheduleAccountUsernameCheck();
      } else {
        accountCheckSequence += 1;
        if (accountCheckTimer) {
          clearTimeout(accountCheckTimer);
          accountCheckTimer = null;
        }
        $("formAccount").value = "";
        setAccountHint("Введіть нік для перевірки.", "idle");
      }
    });
    $("formAccount").addEventListener("input", scheduleAccountUsernameCheck);

    $("ticketClose").addEventListener("click", () => { $("ticketModal").hidden = true; });
    $("replySend").addEventListener("click", sendReply);
    $("ticketBody").addEventListener("click", (event) => {
      const button = event.target.closest("[data-appeal]");
      if (!button) return;
      const rejectionId = button.getAttribute("data-appeal");
      submitAppeal(rejectionId, $("appealText" + rejectionId).value);
    });
    $("ticketList").addEventListener("click", (event) => {
      const card = event.target.closest("[data-ticket]");
      if (card) openTicket(card.getAttribute("data-ticket"));
    });

    window.MSAuth.ready().then((user) => {
      authed = !!(user && user.authenticated);
      $("userBlock").hidden = !authed;
      $("guestBlock").hidden = authed;
      if (authed) {
        loadTickets();
        openBanRequest(user);
      }
    });
  });
})();
