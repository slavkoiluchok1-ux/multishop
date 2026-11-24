/* MultiShop — notifications.js
   Червоні цифри над вкладками (секції) і кількість товарів у кошику.
   Фронтенд опитує сервер кожні кілька секунд. Використовує MS (api.js). */
(function () {
  "use strict";

  const POLL_MS = 10000;
  const ADMIN_BADGE_POLL_MS = 45000;
  let timer = null;
  let adminBadgeTimer = null;
  let adminBadgeRequest = false;
  let lastAdminBadgeCounts = null;

  function paint(nodes, value) {
    const text = value > 99 ? "99+" : String(value);
    nodes.forEach((el) => {
      el.textContent = text;
      el.hidden = value <= 0;
    });
  }

  function hasAdminAccess(user) {
    if (!user || !user.authenticated || user.is_banned) return false;
    const rights = Array.isArray(user.admin_rights) ? user.admin_rights : [];
    return rights.length > 0 || !!user.is_admin_1 || !!user.is_admin_main;
  }

  function ensureAdminBadges() {
    const adminButton = document.querySelector("[data-admin-open]");
    if (adminButton && !adminButton.querySelector("[data-admin-total]")) {
      const badge = document.createElement("span");
      badge.className = "nav-counter is-admin-badge";
      badge.setAttribute("data-admin-total", "");
      badge.setAttribute("aria-label", "Нових: 0");
      badge.hidden = true;
      adminButton.appendChild(badge);
    }

    const menuButton = document.querySelector(".navbar-toggler");
    if (menuButton && !menuButton.querySelector("[data-admin-total]")) {
      const badge = document.createElement("span");
      badge.className = "nav-counter is-admin-badge";
      badge.setAttribute("data-admin-total", "");
      badge.setAttribute("aria-label", "Нових: 0");
      badge.hidden = true;
      menuButton.appendChild(badge);
    }
  }

  function paintAdminBadges(counts) {
    lastAdminBadgeCounts = counts;
    const values = [
      [document.querySelectorAll('[data-counter="admin_complaints"]'), counts.complaints],
      [document.querySelectorAll('[data-counter="admin_support"]'), counts.support],
      [document.querySelectorAll("[data-admin-total]"), counts.total],
    ];
    values.forEach((entry) => {
      const nodes = Array.from(entry[0]);
      const value = entry[1];
      const text = value > 99 ? "99+" : String(value);
      nodes.forEach((el) => {
        el.textContent = text;
        el.hidden = value <= 0;
        el.setAttribute("aria-label", "Нових: " + value);
      });
    });
  }

  function clearAdminBadges() {
    paintAdminBadges({ complaints: 0, support: 0, total: 0 });
  }

  async function refreshAdminBadges() {
    const auth = window.MSAuth;
    const user = auth && auth.state ? auth.state.user : null;
    if (!hasAdminAccess(user)) {
      clearAdminBadges();
      return;
    }
    if (document.hidden || adminBadgeRequest) return;

    adminBadgeRequest = true;
    try {
      const data = await window.MS.get("/admin/badges");
      const currentUser = window.MSAuth && window.MSAuth.state
        ? window.MSAuth.state.user
        : null;
      if (!hasAdminAccess(currentUser)) {
        clearAdminBadges();
        return;
      }
      const complaints = Number(data.complaints);
      const support = Number(data.support);
      const total = Number(data.total);
      if (
        !Number.isSafeInteger(complaints) || complaints < 0 ||
        !Number.isSafeInteger(support) || support < 0 ||
        total !== complaints + support
      ) {
        throw new Error("Сервер повернув некоректні лічильники адміністратора.");
      }
      paintAdminBadges({ complaints: complaints, support: support, total: total });
    } catch (error) {
      console.error("Не вдалося оновити лічильники адміністратора.", error);
      if (error && (error.status === 401 || error.status === 403)) {
        clearAdminBadges();
      }
    } finally {
      adminBadgeRequest = false;
    }
  }

  function syncAdminBadgePolling(user) {
    ensureAdminBadges();
    if (!hasAdminAccess(user)) {
      if (adminBadgeTimer) {
        window.clearInterval(adminBadgeTimer);
        adminBadgeTimer = null;
      }
      clearAdminBadges();
      return;
    }
    refreshAdminBadges();
    if (!adminBadgeTimer) {
      adminBadgeTimer = window.setInterval(() => {
        if (!document.hidden) refreshAdminBadges();
      }, ADMIN_BADGE_POLL_MS);
    }
  }

  async function refresh() {
    // Секції сповіщень (поки лише «Обране» має події)
    try {
      const data = await window.MS.get("/notifications/counters");
      const counters = (data && data.counters) || {};
      document.querySelectorAll("[data-counter]").forEach((el) => {
        const section = el.getAttribute("data-counter");
        if (section === "admin_complaints" || section === "admin_support") return;
        paint([el], Number(counters[section] || 0));
      });
    } catch (e) {
      /* гість або політику ще не прийнято — цифри просто не показуємо */
    }

    // Кількість товарів у кошику
    try {
      const cart = await window.MS.get("/cart");
      const count = Number((cart && cart.count) || 0);
      paint(Array.from(document.querySelectorAll("[data-cart-count]")), count);
    } catch (e) {
      /* мережа недоступна */
    }
  }

  function start() {
    ensureAdminBadges();
    refresh();
    if (!timer) timer = window.setInterval(refresh, POLL_MS);
    const auth = window.MSAuth;
    if (auth && auth.state && auth.state.user) {
      syncAdminBadgePolling(auth.state.user);
    }
  }

  document.addEventListener("DOMContentLoaded", start);
  document.addEventListener("ms:auth", (event) => {
    syncAdminBadgePolling(event.detail);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAdminBadges();
  });
  document.addEventListener("click", (event) => {
    if (
      event.target &&
      event.target.closest &&
      event.target.closest("[data-admin-open], [data-mobile-admin-gear]") &&
      lastAdminBadgeCounts
    ) {
      window.setTimeout(() => paintAdminBadges(lastAdminBadgeCounts), 0);
    }
  });

  window.MSCounters = { refresh: refresh };
})();
