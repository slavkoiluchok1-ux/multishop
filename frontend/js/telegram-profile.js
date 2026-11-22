/* MultiShop — Telegram connection and category preferences in the profile. */
(function () {
  "use strict";

  const statusText = document.getElementById("telegramProfileStatus");
  if (!statusText || !window.MS) return;

  const errorBox = document.getElementById("telegramProfileError");
  const successBox = document.getElementById("telegramProfileSuccess");
  const connectButton = document.getElementById("telegramProfileConnect");
  const unlinkButton = document.getElementById("telegramProfileUnlink");
  const pendingBox = document.getElementById("telegramProfilePending");
  const codeText = document.getElementById("telegramProfileCode");
  const deepLink = document.getElementById("telegramProfileDeepLink");
  const copyButton = document.getElementById("telegramProfileCopy");
  const countdown = document.getElementById("telegramProfileCountdown");
  const preferences = document.getElementById("telegramProfilePreferences");
  let pending = null;
  let expiresAt = 0;
  let timer = 0;

  function showError(message) {
    errorBox.textContent = message || "Не вдалося виконати дію.";
    errorBox.hidden = !message;
    successBox.hidden = true;
  }

  function showSuccess(message) {
    successBox.textContent = message || "";
    successBox.hidden = !message;
    errorBox.hidden = true;
  }

  function renderPreferences(data) {
    preferences.replaceChildren();
    (data.categories || []).forEach((category) => {
      const wrapper = document.createElement("div");
      wrapper.className = "col-md-6";
      const label = document.createElement("label");
      label.className = "form-check";
      const input = document.createElement("input");
      input.className = "form-check-input";
      input.type = "checkbox";
      input.checked = (data.prefs || {})[category.key] !== false;
      input.dataset.telegramPreference = category.key;
      const text = document.createElement("span");
      text.className = "form-check-label";
      text.textContent = category.label;
      label.append(input, text);
      wrapper.appendChild(label);
      preferences.appendChild(wrapper);
    });
    preferences.hidden = !data.connected || !data.configured;
  }

  function render(data) {
    if (!data.configured) {
      statusText.textContent = "Telegram-бот недоступний: адміністратор ще не налаштував токен.";
    } else if (data.connected) {
      statusText.textContent = data.telegram_username
        ? "Підключено як @" + data.telegram_username + "."
        : "Telegram підключено.";
    } else {
      statusText.textContent = "Telegram не підключено.";
    }
    connectButton.hidden = !data.configured || data.connected || !!pending;
    unlinkButton.hidden = !data.connected;
    renderPreferences(data);
  }

  async function refresh() {
    const data = await window.MS.get("/telegram/status");
    render(data);
    if (data.connected) {
      pending = null;
      pendingBox.hidden = true;
    }
  }

  function startCountdown() {
    if (timer) window.clearInterval(timer);
    timer = window.setInterval(() => {
      const seconds = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      countdown.textContent = seconds
        ? "Залишилося " + Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0")
        : "Строк дії коду минув. Створіть новий код.";
      if (!seconds) {
        window.clearInterval(timer);
        timer = 0;
        pending = null;
        connectButton.hidden = false;
      }
    }, 1000);
  }

  connectButton.addEventListener("click", async () => {
    connectButton.disabled = true;
    showError("");
    try {
      pending = await window.MS.post("/telegram/link", {});
      codeText.textContent = pending.code;
      deepLink.href = pending.deep_link;
      deepLink.target = "_self";
      expiresAt = Date.now() + pending.expires_in_seconds * 1000;
      pendingBox.hidden = false;
      connectButton.hidden = true;
      startCountdown();
    } catch (error) {
      showError(error.message);
    } finally {
      connectButton.disabled = false;
    }
  });

  copyButton.addEventListener("click", async () => {
    if (!pending) return;
    try {
      await navigator.clipboard.writeText(pending.code);
      showSuccess("Код скопійовано.");
    } catch (_error) {
      showError("Не вдалося скопіювати код. Виділіть його та скопіюйте вручну.");
    }
  });

  unlinkButton.addEventListener("click", async () => {
    unlinkButton.disabled = true;
    showError("");
    try {
      await window.MS.post("/telegram/unlink", {});
      pending = null;
      await refresh();
      showSuccess("Telegram відключено.");
    } catch (error) {
      showError(error.message);
    } finally {
      unlinkButton.disabled = false;
    }
  });

  preferences.addEventListener("change", async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.dataset.telegramPreference) return;
    input.disabled = true;
    showError("");
    try {
      await window.MS.request("/telegram/prefs", {
        method: "PATCH",
        body: { prefs: { [input.dataset.telegramPreference]: input.checked } },
      });
      showSuccess("Налаштування збережено.");
    } catch (error) {
      input.checked = !input.checked;
      showError(error.message);
    } finally {
      input.disabled = false;
    }
  });

  refresh().catch((error) => showError(error.message));
  window.setInterval(() => {
    if (pending) {
      refresh().catch((error) => showError(error.message));
    }
  }, 3000);
})();
