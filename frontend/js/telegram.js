/* MultiShop — secure account linking and Telegram connection status. */
(function () {
  "use strict";

  const dialog = document.getElementById("telegramDialog");
  if (!dialog || !window.MS) return;

  const statusText = dialog.querySelector("[data-telegram-status]");
  const errorBox = dialog.querySelector("[data-telegram-error]");
  const pendingBox = dialog.querySelector("[data-telegram-pending]");
  const codeButton = dialog.querySelector("[data-telegram-code]");
  const deepLink = dialog.querySelector("[data-telegram-link]");
  const connectButton = dialog.querySelector("[data-telegram-connect]");
  const disconnectButton = dialog.querySelector("[data-telegram-disconnect]");
  const checkButton = dialog.querySelector("[data-telegram-check]");
  let connection = { configured: false, connected: false, telegram_username: "" };
  let botUrl = "";
  let pending = null;
  let statusTimer = 0;

  function currentUser() {
    return window.MSAuth && window.MSAuth.state
      ? window.MSAuth.state.user
      : null;
  }

  function isAuthenticated() {
    const user = currentUser();
    return !!(user && user.authenticated && !user.is_banned);
  }

  function render() {
    const connected = connection.connected;
    botUrl = connection.bot_url || "";
    document.querySelectorAll("[data-telegram-button-label]").forEach((label) => {
      label.textContent = connected ? "Бот підключений" : "Підключити Telegram";
    });
    document.querySelectorAll("[data-telegram-open]:not([data-telegram-header])").forEach((button) => {
      button.classList.toggle("btn-success", connected);
      button.classList.toggle("btn-primary", !connected);
      button.classList.toggle("is-connected", connected);
      button.setAttribute("aria-label", "Керування Telegram-ботом");
    });
    const homeButton = document.querySelector(".home-telegram-control");
    if (homeButton) {
      const plane = homeButton.querySelector(".home-telegram-plane");
      const dot = homeButton.querySelector(".home-telegram-dot");
      if (plane) plane.hidden = connected;
      if (dot) dot.hidden = !connected;
    }

    const headerButton = document.querySelector("[data-telegram-header]");
    if (headerButton) {
      const label = connected ? "Telegram підключено" : "Підключити Telegram";
      headerButton.dataset.tooltip = label;
      headerButton.setAttribute("aria-label", label);
      headerButton.removeAttribute("title");
      headerButton.setAttribute("aria-pressed", String(connected));
      headerButton.href = connected && botUrl ? botUrl : "/?telegram=open";
      headerButton.target = "_blank";
      headerButton.rel = "noopener noreferrer";
    }

    if (!isAuthenticated()) {
      statusText.textContent = "Увійдіть до акаунта, щоб підключити Telegram-бота.";
    } else if (connected) {
      statusText.textContent = connection.telegram_username
        ? "Підключено до Telegram @" + connection.telegram_username + "."
        : "Telegram-бот підключений.";
    } else if (!connection.configured) {
      statusText.textContent = "Telegram-бот ще не налаштований адміністратором.";
    } else if (pending) {
      statusText.textContent = "Залишилося підключити акаунт у Telegram.";
    } else {
      statusText.textContent = "Підключіть бота, щоб отримувати сповіщення та переглядати дані акаунта.";
    }
    pendingBox.hidden = connected || !pending;
    connectButton.hidden = !isAuthenticated() || connected || !connection.configured || !!pending;
    disconnectButton.hidden = !connected;
    checkButton.hidden = !pending || connected;
  }

  async function refreshStatus() {
    if (!isAuthenticated()) {
      connection = { configured: false, connected: false, telegram_username: "" };
      pending = null;
      render();
      return;
    }
    const data = await window.MS.get("/telegram/status");
    connection = data;
    if (connection.connected) pending = null;
    render();
  }

  function showError(message) {
    errorBox.textContent = message || "Не вдалося виконати дію.";
    errorBox.hidden = false;
  }

  async function openDialog() {
    errorBox.hidden = true;
    if (!dialog.open) dialog.showModal();
    if (!statusTimer) {
      statusTimer = window.setInterval(() => {
        refreshStatus().catch((error) => {
          showError(error.message);
        });
      }, 3000);
    }
    try {
      await refreshStatus();
    } catch (error) {
      showError(error.message);
    }
  }

  window.MSTelegramOpen = openDialog;
  window.MSTelegramOpenChat = async function () {
    await openDialog();
  };

  document.querySelectorAll("[data-telegram-open]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      window.MSTelegramOpenChat();
    });
  });
  dialog.querySelectorAll("[data-telegram-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  connectButton.addEventListener("click", async () => {
    connectButton.disabled = true;
    errorBox.hidden = true;
    try {
      pending = await window.MS.post("/telegram/link", {});
      codeButton.textContent = pending.code;
      deepLink.href = pending.deep_link;
      deepLink.target = "_self";
      render();
    } catch (error) {
      showError(error.message);
    } finally {
      connectButton.disabled = false;
    }
  });

  codeButton.addEventListener("click", async () => {
    if (!pending) return;
    try {
      await navigator.clipboard.writeText(pending.code);
      window.MS.toast("Код підключення скопійовано.", "success");
    } catch (_error) {
      showError("Не вдалося скопіювати код. Виділіть його та скопіюйте вручну.");
    }
  });

  checkButton.addEventListener("click", async () => {
    errorBox.hidden = true;
    try {
      await refreshStatus();
    } catch (error) {
      showError(error.message);
    }
  });

  disconnectButton.addEventListener("click", async () => {
    disconnectButton.disabled = true;
    errorBox.hidden = true;
    try {
      await window.MS.post("/telegram/unlink", {});
      pending = null;
      await refreshStatus();
    } catch (error) {
      showError(error.message);
    } finally {
      disconnectButton.disabled = false;
    }
  });

  dialog.addEventListener("close", () => {
    if (statusTimer) {
      window.clearInterval(statusTimer);
      statusTimer = 0;
    }
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });

  function syncAuth() {
    if (isAuthenticated()) {
      refreshStatus().catch((error) => {
        console.error("Не вдалося перевірити підключення Telegram.", error);
      });
    } else {
      connection = { configured: false, connected: false, telegram_username: "" };
      pending = null;
      render();
    }
  }

  if (new URLSearchParams(window.location.search).get("telegram") === "open") {
    window.MSAuth.ready().then(() => {
      openDialog();
      const url = new URL(window.location.href);
      url.searchParams.delete("telegram");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }).catch((error) => {
      showError(error.message);
    });
  }

  dialog.addEventListener("close", render);
  document.addEventListener("DOMContentLoaded", syncAuth);
  document.addEventListener("ms:auth", syncAuth);
})();
