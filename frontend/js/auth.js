/* MultiShop — auth.js
   Стан користувача, оновлення навігації, вихід. Використовує MS (api.js). */
(function () {
  "use strict";

  const state = { user: null, ready: null };
  const NAV_CACHE_KEY = "ms_nav_cache";
  let balanceText = ""; // коротка форма балансу для кешу шапки

  const headerTooltip = {
    element: null,
    timer: 0,

    ensure() {
      if (this.element) return this.element;
      const element = document.createElement("div");
      element.className = "navbar-header-tooltip";
      element.setAttribute("role", "tooltip");
      element.hidden = true;
      document.body.appendChild(element);
      this.element = element;
      return element;
    },

    text(button) {
      return (button.dataset.tooltip ||
        button.getAttribute("aria-label") ||
        "").trim();
    },

    position(button) {
      const element = this.ensure();
      const text = this.text(button);
      if (!text) return;
      element.textContent = text;
      element.hidden = false;

      const rect = button.getBoundingClientRect();
      const width = element.offsetWidth;
      const height = element.offsetHeight;
      const left = Math.max(8, Math.min(
        window.innerWidth - width - 8,
        rect.left + (rect.width - width) / 2
      ));
      const top = rect.bottom + 8;
      const center = rect.left + rect.width / 2;
      const arrowX = Math.max(8, Math.min(width - 8, center - left));

      element.style.left = left + "px";
      element.style.top = top + "px";
      element.style.setProperty("--tooltip-arrow-x", arrowX + "px");
    },

    show(button) {
      if (window.matchMedia("(hover: none), (pointer: coarse)").matches) return;
      this.position(button);
    },

    hide() {
      window.clearTimeout(this.timer);
      this.timer = 0;
      if (this.element) this.element.hidden = true;
    },

    attach(button) {
      if (!button || button.dataset.headerTooltipAttached === "true") return;
      button.dataset.headerTooltipAttached = "true";
      button.removeAttribute("title");

      button.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "touch") return;
        window.clearTimeout(this.timer);
        this.timer = window.setTimeout(() => this.show(button), 300);
      });
      button.addEventListener("pointerleave", () => this.hide());
      button.addEventListener("focus", () => {
        if (button.matches(":focus-visible")) this.show(button);
      });
      button.addEventListener("blur", () => this.hide());
      button.addEventListener("keydown", () => this.hide());
    },
  };

  function readCachedUser() {
    try {
      const cached = JSON.parse(window.localStorage.getItem(NAV_CACHE_KEY) || "null");
      if (!cached || typeof cached.authenticated !== "boolean") return null;
      return cached;
    } catch (e) {
      return null;
    }
  }

  function clearCache() {
    try {
      window.localStorage.removeItem(NAV_CACHE_KEY);
    } catch (e) {
      /* Storage may be disabled — нічого не робимо. */
    }
    try {
      for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
        const key = window.sessionStorage.key(i);
        if (key && key.startsWith("ms_seller_verified:")) {
          window.sessionStorage.removeItem(key);
        }
      }
    } catch (e) {
      /* Session storage may be disabled. */
    }
  }

  /* Роль і права одним рядком — щоб помітити їх зміну й почистити кеш. */
  function userSignature(user) {
    const rights = (user && Array.isArray(user.admin_rights)) ? user.admin_rights : [];
    return [
      user && user.is_admin_main ? "1" : "0",
      user && user.is_admin_1 ? "1" : "0",
      user && user.is_admin_2 ? "1" : "0",
      user && user.is_banned ? "1" : "0",
      rights.slice().sort().join(","),
    ].join("|");
  }

  function cacheUser(user) {
    // Гостей не кешуємо: без кешу шапка одразу гостьова.
    if (!user || !user.authenticated) {
      clearCache();
      return;
    }
    const cached = {
      authenticated: true,
      policy_accepted: !!user.policy_accepted,
      is_admin_main: !!user.is_admin_main,
      is_admin_1: !!user.is_admin_1,
      is_admin_2: !!user.is_admin_2,
      is_banned: !!user.is_banned,
      admin_rights: Array.isArray(user.admin_rights) ? user.admin_rights : [],
      username: user.username ? user.username : "",
      avatar: user.avatar ? user.avatar : null,
      balance: balanceText,
    };
    try {
      window.localStorage.setItem(NAV_CACHE_KEY, JSON.stringify(cached));
    } catch (e) {
      /* Storage may be disabled; navigation still follows the live session. */
    }
  }

  /* Сторінки, доступні забаненому: Головна, Політика, Підтримка, вхід/реєстрація. */
  const BANNED_ALLOWED_PAGES = [
    "/",
    "/pages/policy.html",
    "/pages/support.html",
    "/pages/login.html",
    "/pages/register.html",
  ];

  function isBannedAllowedPage() {
    const path = window.location.pathname;
    return BANNED_ALLOWED_PAGES.some((allowed) => path === allowed);
  }

  /* Плитки спливаючого вікна «⚙️ Адмін». Лише сторінки, що вже є в проєкті. */
  const ADMIN_TILES = [
    {
      icon: "🛡️",
      label: "Кабінет адміна",
      href: "/pages/admin/cabinet.html",
      show: () => true,
    },
    {
      icon: "🛒",
      label: "Товари",
      href: "/pages/admin/products.html",
      show: (user, rights) => rights.indexOf("products") !== -1,
    },
    {
      icon: "🚩",
      label: "Скарги",
      href: "/pages/admin/complaints.html",
      counter: "admin_complaints",
      // Право на розгляд залежить від handler_rights типів скарг
      show: (user, rights) => canReviewComplaints(rights),
    },
    {
      icon: "⛔",
      label: "Бан",
      href: "/pages/admin/bans.html",
      show: (user, rights) => rights.indexOf("bans") !== -1,
    },
    {
      icon: "🆘",
      label: "Підтримка",
      href: "/pages/admin/support.html",
      counter: "admin_support",
      show: (user, rights) =>
        rights.indexOf("support") !== -1 || !!user.is_admin_1 || !!user.is_admin_main,
    },
    {
      icon: "🪙",
      label: "Видача MC",
      href: "/pages/admin/coins.html",
      show: (user) => !!user.is_admin_main,
    },
    {
      icon: "⚙️",
      label: "Django-адмінка",
      href: "/admin/",
      show: (user) => !!user.is_admin_main,
    },
    {
      icon: "✏️",
      label: "Редагувати головну",
      href: "/?edit=1",
      show: (user, rights) => rights.indexOf("home") !== -1,
    },
  ];

  function canReviewComplaints(rights) {
    return ["complaints", "products", "bans"].some(
      (right) => rights.indexOf(right) !== -1
    );
  }

  /* Кнопка «⚙️ Адмін» — лише для адмінів із правами (не для забанених). */
  function adminAccess(user) {
    if (!user || !user.authenticated || user.is_banned) return false;
    const rights = Array.isArray(user.admin_rights) ? user.admin_rights : [];
    return rights.length > 0 || !!user.is_admin_1 || !!user.is_admin_main;
  }

  function setNavSlotVisibility(element, visible) {
    element.hidden = false;
    element.style.visibility = visible ? "visible" : "hidden";
    element.setAttribute("aria-hidden", String(!visible));
    element.tabIndex = visible ? 0 : -1;
  }

  function setupHeaderTooltips() {
    const selectors = [
      "[data-telegram-header]",
      "[data-wallet-balance]",
      "[data-admin-coin]",
    ];
    document.querySelectorAll(selectors.join(",")).forEach((button) => {
      headerTooltip.attach(button);
    });
  }

  /* Значок монети ліворуч (лише головний) і кнопка «⚙️ Адмін» праворуч. */
  function ensureAdminNav() {
    const container = document.querySelector(".navbar .container");
    if (container && !container.querySelector("[data-admin-coin]")) {
      const coin = document.createElement("a");
      coin.className = "nav-link nav-pill navbar-coin";
      coin.href = "/pages/admin/coins.html";
      coin.setAttribute("data-admin-coin", "");
      coin.dataset.tooltip = "Видача MC";
      coin.setAttribute("aria-label", "Видача MC");
      coin.textContent = "🪙";
      setNavSlotVisibility(coin, false);
      container.insertBefore(coin, container.firstChild);
    }
    ensureAdminButton();
    ensureAdminMenu();
  }

  function ensureAdminButton() {
    const ul = document.querySelector(".navbar .navbar-nav");
    if (!ul || ul.querySelector("[data-admin-button]")) return;
    const li = document.createElement("li");
    li.className = "nav-item";
    li.setAttribute("data-auth", "user");
    li.setAttribute("data-admin-button", "");
    li.hidden = true;
    li.innerHTML =
      '<button class="nav-link nav-pill border-0 bg-transparent" type="button" data-admin-open>⚙️ Адмін</button>';
    const logout = ul.querySelector("[data-logout]");
    const logoutLi = logout ? logout.closest("li") : null;
    if (logoutLi) {
      ul.insertBefore(li, logoutLi);
    } else {
      ul.appendChild(li);
    }
  }

  function ensureAdminMenu() {
    if (document.getElementById("msAdminMenu")) return;
    const modal = document.createElement("div");
    modal.className = "app-modal";
    modal.id = "msAdminMenu";
    modal.hidden = true;
    modal.innerHTML =
      '<div class="app-modal-box" role="dialog" aria-modal="true" aria-label="Адмінські розділи">' +
        '<button class="app-modal-close" type="button" data-admin-menu-close aria-label="Закрити">×</button>' +
        '<p class="eyebrow mb-1">Адміністрування</p>' +
        '<h2 class="h5 mb-3">Панель адміністратора</h2>' +
        '<div class="admin-menu-grid" data-admin-menu-tiles></div>' +
      "</div>";
    document.body.appendChild(modal);
  }

  function renderAdminTiles() {
    const box = document.querySelector("[data-admin-menu-tiles]");
    if (!box) return;
    const user = state.user || {};
    const rights = Array.isArray(user.admin_rights) ? user.admin_rights : [];
    const esc = window.MS.escapeHtml;
    box.innerHTML = ADMIN_TILES.filter((tile) => tile.show(user, rights))
      .map((tile) => {
        const counter = tile.counter
          ? '<span class="nav-counter" data-counter="' + esc(tile.counter) + '" hidden>0</span>'
          : "";
        return (
          '<a class="admin-tile" href="' + esc(tile.href) + '">' +
            '<span class="admin-tile-icon">' + tile.icon + "</span>" +
            '<span class="admin-tile-label">' + esc(tile.label) + "</span>" +
            counter +
          "</a>"
        );
      })
      .join("");
  }

  function openAdminMenu() {
    const modal = document.getElementById("msAdminMenu");
    if (!modal) return;
    renderAdminTiles();
    modal.hidden = false;
    if (window.MSCounters) window.MSCounters.refresh();
  }

  function closeAdminMenu() {
    const modal = document.getElementById("msAdminMenu");
    if (modal) modal.hidden = true;
  }

  function showBanNotice(user) {
    if (window.location.pathname !== "/" || !user || !user.is_banned || !user.active_ban) return;
    let modal = document.getElementById("msBanNotice");
    if (!modal) {
      modal = document.createElement("div");
      modal.className = "app-modal";
      modal.id = "msBanNotice";
      const box = document.createElement("div");
      box.className = "app-modal-box";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "msBanNoticeTitle");
      const title = document.createElement("h2");
      title.className = "h5 mb-2";
      title.id = "msBanNoticeTitle";
      title.textContent = "Доступ до акаунта обмежено";
      const description = document.createElement("p");
      description.className = "text-muted-soft";
      description.textContent = "Причина блокування:";
      const reason = document.createElement("p");
      reason.className = "ban-notice-reason";
      reason.id = "msBanNoticeReason";
      const actions = document.createElement("div");
      actions.className = "d-flex flex-wrap gap-2";
      const support = document.createElement("a");
      support.className = "btn btn-primary";
      support.id = "msBanNoticeSupport";
      support.textContent = "Оскаржити в підтримці";
      const close = document.createElement("button");
      close.className = "btn btn-outline-primary";
      close.type = "button";
      close.textContent = "Зрозуміло";
      close.addEventListener("click", () => { modal.hidden = true; });
      actions.append(support, close);
      box.append(title, description, reason, actions);
      modal.appendChild(box);
      document.body.appendChild(modal);
    }
    document.getElementById("msBanNoticeReason").textContent =
      user.active_ban.reason || "Причину не вказано.";
    document.getElementById("msBanNoticeSupport").href =
      "/pages/support.html?ban_id=" + encodeURIComponent(String(user.active_ban.id));
    modal.hidden = false;
  }

  /* Значок балансу зліва у шапці (бачать усі, крім гостей і забанених). */
  function ensureWalletBadge() {
    const container = document.querySelector(".navbar .container");
    if (!container || container.querySelector("[data-wallet-balance]")) return;
    const badge = document.createElement("a");
    badge.className = "nav-link nav-pill navbar-coin";
    badge.href = "/pages/profile.html";
    badge.setAttribute("data-wallet-balance", "");
    badge.dataset.tooltip = "Доступні MC";
    badge.setAttribute("aria-label", "Доступні MC");
    badge.textContent = balanceText || "🪙 — MC";
    const coin = container.querySelector("[data-admin-coin]");
    if (coin) {
      container.insertBefore(badge, coin);
    } else {
      container.insertBefore(badge, container.firstChild);
    }
    setNavSlotVisibility(badge, walletVisible());
  }

  function ensureTelegramHeader() {
    const container = document.querySelector(".navbar .container");
    const wallet = container && container.querySelector("[data-wallet-balance]");
    if (!container || container.querySelector("[data-telegram-header]")) return;
    const link = document.createElement("a");
    link.className = "nav-link nav-pill navbar-telegram";
    link.href = "/?telegram=open";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.tooltip = "Відкрити Telegram-бота";
    link.setAttribute("data-telegram-header", "");
    link.setAttribute("aria-label", "Відкрити Telegram-бота");
    link.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
        '<path d="M21.6 2.4 3.1 9.6c-.9.4-.8 1.7.1 2l6.3 2.1 2.1 6.3c.3.9 1.6 1 2 .1l7.2-18.5c.3-.8-.4-1.5-1.2-1.2ZM10.8 12.1l7-6.1-6.1 7Z"/>' +
      "</svg>";
    link.addEventListener("click", async (event) => {
      if (link.hasAttribute("data-telegram-header")) {
        event.preventDefault();
        try {
          const status = await window.MS.get("/telegram/status");
          if (status.connected && status.bot_url) {
            window.open(status.bot_url, "_blank", "noopener,noreferrer");
          } else if (status.configured) {
            const pending = await window.MS.post("/telegram/link", {});
            if (pending.deep_link) window.open(pending.deep_link, "_blank", "noopener,noreferrer");
          } else if (typeof window.MSTelegramOpen === "function") {
            window.MSTelegramOpen();
          } else {
            window.location.assign("/?telegram=open");
          }
        } catch (_error) {
          if (typeof window.MSTelegramOpen === "function") {
            window.MSTelegramOpen();
          } else {
            window.location.assign("/?telegram=open");
          }
        }
        return;
      }
      if (typeof window.MSTelegramOpenChat === "function") {
        event.preventDefault();
        window.MSTelegramOpenChat();
        return;
      }
      if (typeof window.MSTelegramOpen === "function") {
        event.preventDefault();
        window.MSTelegramOpen();
        return;
      }
      event.preventDefault();
      try {
        const status = await window.MS.get("/telegram/status");
        if (status.connected && status.bot_url) {
          window.location.assign(status.bot_url);
        } else if (status.configured) {
          const pending = await window.MS.post("/telegram/link", {});
          if (pending.deep_link) window.location.assign(pending.deep_link);
        } else {
          window.location.assign("/?telegram=open");
        }
      } catch (_error) {
        window.location.assign("/?telegram=open");
      }
    });
    container.insertBefore(link, wallet || container.firstChild);
  }

  /* Лише рядки, без float: до 2 знаків після коми, тисячі — через пробіл. */
  function formatAmount(value) {
    let raw = String(value === null || value === undefined ? "0" : value).trim();
    let sign = "";
    if (raw.charAt(0) === "-") {
      sign = "-";
      raw = raw.slice(1);
    }
    const parts = raw.split(".");
    const int = (parts[0] || "0").replace(/^0+(?=\d)/, "") || "0";
    const frac = (parts[1] || "").slice(0, 2).replace(/0+$/, "");
    return sign + int.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (frac ? "." + frac : "");
  }

  function walletVisible() {
    const user = state.user;
    return !!(user && user.authenticated && !user.is_banned && user.policy_accepted !== false);
  }

  async function refreshWallet() {
    const badge = document.querySelector("[data-wallet-balance]");
    if (!badge) return;
    if (!walletVisible()) {
      setNavSlotVisibility(badge, false);
      return;
    }
    setNavSlotVisibility(badge, true);
    try {
      const data = await window.MS.get("/wallet");
      const balance = (data && data.balance) || {};
      balanceText = "🪙 " + formatAmount(balance.free_balance_mc) + " MC";
      badge.textContent = balanceText;
      badge.dataset.tooltip =
        "Доступні MC. Утримані: " + balance.held_mc +
        ", заморожені: " + balance.frozen_mc +
        ", застава: " + balance.deposit_locked_mc;
      badge.setAttribute("aria-label", badge.dataset.tooltip);
      headerTooltip.attach(badge);
      cacheUser(state.user);
    } catch (e) {
      // Лишаємо «— MC», помилку на сторінці не показуємо.
    }
  }

  function isAuthed() {
    return !!(state.user && state.user.authenticated);
  }

  function ensureUserAvatarFallback(image) {
    const parent = image.parentElement;
    if (!parent) return null;
    let fallback = parent.querySelector("[data-user-avatar-fallback]");
    if (!fallback) {
      fallback = document.createElement("span");
      fallback.className = "avatar avatar-placeholder";
      fallback.setAttribute("data-user-avatar-fallback", "");
      fallback.setAttribute("aria-hidden", "true");
      parent.insertBefore(fallback, image);
    }
    return fallback;
  }

  function applyNav(user) {
    const authed = !!(user && user.authenticated);
    const banned = !!(user && user.is_banned);
    cacheUser(user);
    ensureAdminNav();

    document.querySelectorAll('[data-auth="guest"]').forEach((el) => {
      el.hidden = authed;
    });
    document.querySelectorAll('[data-auth="user"]').forEach((el) => {
      if (!authed) {
        el.hidden = true;
        return;
      }
      // Забанений бачить лише Головну, Підтримку та Політику.
      // Вихід і бейдж профілю лишаємо доступними.
      const keep = el.querySelector("[data-logout]") || el.querySelector("[data-user-name]");
      el.hidden = banned && !keep;
    });

    document.querySelectorAll("[data-user-name]").forEach((el) => {
      el.textContent = authed ? user.username : "";
    });

    document.querySelectorAll("[data-user-avatar]").forEach((el) => {
      const fallback = ensureUserAvatarFallback(el);
      if (authed && user.avatar) {
        el.src = user.avatar;
        el.hidden = false;
        if (fallback) fallback.hidden = true;
      } else {
        el.hidden = true;
        if (fallback) {
          const username = authed && user.username ? user.username.trim() : "";
          fallback.textContent = Array.from(username)[0]
            ? Array.from(username)[0].toLocaleUpperCase()
            : "?";
          fallback.hidden = !authed;
        }
      }
    });

    // Одна кнопка «⚙️ Адмін» замість окремих адмінських вкладок
    const allowed = adminAccess(user);
    document.querySelectorAll("[data-admin-button]").forEach((el) => {
      el.hidden = !allowed;
    });
    if (!allowed) closeAdminMenu();

    document.querySelectorAll("[data-admin-coin]").forEach((el) => {
      setNavSlotVisibility(el, authed && !banned && !!user.is_admin_main);
    });

    ensureWalletBadge();
    ensureTelegramHeader();
    setupHeaderTooltips();
    if (!window.location.pathname.endsWith("/pages/seller.html")) {
      refreshWallet();
    }

    document.querySelectorAll("[data-policy-warning]").forEach((el) => {
      el.hidden = !(authed && user.policy_accepted === false);
    });

    document.documentElement.dataset.authReady = "true";
  }

  function setUser(user) {
    state.user = user;
    applyNav(user);
    showBanNotice(user);
    return user;
  }

  /* Гостьовий кошик зливаємо з кошиком акаунта один раз після входу.
     Прапорець ставить сторінка входу/реєстрації. */
  async function mergeCart(user) {
    if (!user || !user.authenticated) return;
    if (user.policy_accepted === false) return; // чекаємо прийняття політики
    if (window.localStorage.getItem("ms_merge_cart") !== "1") return;
    try {
      await window.MS.post("/cart/merge", {});
      window.localStorage.removeItem("ms_merge_cart");
      if (window.MSCounters) window.MSCounters.refresh();
    } catch (e) {
      /* політику ще не прийнято або мережа — спробуємо наступного разу */
    }
  }

  async function refresh(options) {
    const strict = !!(options && options.strict);
    let user;
    try {
      user = await window.MS.get("/auth/me");
    } catch (e) {
      if (strict) {
        clearCache();
        state.user = { authenticated: false, policy_accepted: false };
        applyNav(null);
        throw e;
      }
      if (e && e.status === 401) {
        user = { authenticated: false, policy_accepted: false };
      } else if (state.user && state.user.authenticated) {
        user = state.user; // мережа недоступна — лишаємо кешовану шапку
      } else {
        user = { authenticated: false, policy_accepted: false };
      }
    }
    const cached = readCachedUser();
    // Кеш чистимо, коли сесії немає або змінилися роль чи права.
    if (!user.authenticated || (cached && userSignature(cached) !== userSignature(user))) {
      clearCache();
    }
    state.user = user;
    applyNav(user);
    showBanNotice(user);
    // Забанений бачить лише Головну, Підтримку й Політику.
    if (state.user && state.user.is_banned && !isBannedAllowedPage()) {
      window.location.href = "/";
      return state.user;
    }
    await mergeCart(state.user);
    document.dispatchEvent(new CustomEvent("ms:auth", { detail: state.user }));
    return state.user;
  }

  function ready() {
    if (!state.ready) state.ready = refresh();
    return state.ready;
  }

  function postLoginFallback(user) {
    if (!user || user.policy_accepted !== true) return "/pages/policy.html?required=1";
    const next = new URLSearchParams(window.location.search).get("next");
    if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return "/";
    try {
      const target = new URL(next, window.location.origin);
      if (target.origin !== window.location.origin) return "/";
      if (["/pages/login.html", "/pages/register.html", "/pages/policy.html"].includes(target.pathname)) {
        return "/";
      }
      return target.pathname + target.search + target.hash;
    } catch (error) {
      return "/";
    }
  }

  async function logout() {
    clearCache();
    try {
      await window.MS.post("/auth/logout");
      setUser({ authenticated: false, policy_accepted: false });
    } catch (e) {
      /* виходимо навіть якщо запит не вдався */
    }
    window.location.href = "/pages/login.html";
  }

  function bind() {
    document.querySelectorAll("[data-logout]").forEach((el) => {
      el.addEventListener("click", (event) => {
        event.preventDefault();
        logout();
      });
    });

    // Єдине вікно «⚙️ Адмін»: відкриття, закриття (хрестик, Esc, клік поза вікном).
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-admin-open]")) {
        event.preventDefault();
        openAdminMenu();
        return;
      }
      if (event.target.closest("[data-admin-menu-close]")) {
        closeAdminMenu();
        return;
      }
      const modal = document.getElementById("msAdminMenu");
      if (modal && event.target === modal) closeAdminMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAdminMenu();
    });
  }

  const cachedUser = readCachedUser();
  if (cachedUser) {
    balanceText = cachedUser.balance || "";
    state.user = cachedUser;
    applyNav(cachedUser);
  } else {
    // Немає кешу — одразу показуємо гостьову шапку і ставимо data-auth-ready,
    // щоб елементи [data-auth="guest"] не «стрибали» після відповіді /auth/me.
    applyNav(null);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    ready();
  });

  // Купівля, підтвердження замовлення чи видача MC — оновлюємо баланс.
  window.addEventListener("ms:wallet-changed", refreshWallet);

  window.MSAuth = {
    state: state,
    isAuthed: isAuthed,
    ready: ready,
    refresh: refresh,
    postLoginFallback: postLoginFallback,
    logout: logout,
    applyNav: applyNav,
    setUser: setUser,
  };
  window.MSHeaderTooltip = headerTooltip;
})();
