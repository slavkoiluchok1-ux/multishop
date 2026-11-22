/* Швидкі посилання головної у бічних полях; права фільтруються за /api/auth/me. */
(function () {
  "use strict";

  const SPRITE = "/static/svg/floating-sprite.svg";
  const entries = [
    { key: "home", label: "Головна", href: "/", symbol: "float-home", public: true },
    { key: "policy", label: "Політика", href: "/pages/policy.html", symbol: "float-policy", public: true },
    { key: "catalog", label: "Каталог", href: "/pages/catalog.html", symbol: "float-catalog", public: true },
    { key: "cart", label: "Кошик", href: "/pages/cart.html", symbol: "float-cart", public: true, counter: "cart" },
    { key: "favorites", label: "Обране", href: "/pages/favorites.html", symbol: "float-favorites", auth: true, counter: "favorites" },
    { key: "orders", label: "Замовлення", href: "/pages/orders.html", symbol: "float-orders", auth: true, counter: "orders" },
    { key: "complaints", label: "Скарги", href: "/pages/complaints.html", symbol: "float-complaints", auth: true, counter: "complaints" },
    { key: "seller", label: "Ваші товари", href: "/pages/seller.html", symbol: "float-seller", auth: true },
    { key: "support", label: "Підтримка", href: "/pages/support.html", symbol: "float-support", public: true },
    { key: "profile", label: "Профіль", href: "/pages/profile.html", symbol: "float-profile", auth: true },
    { key: "telegram", label: "Telegram", href: "/?telegram=open", symbol: "float-telegram", auth: true },
    { key: "ban", label: "Бан", href: "/pages/admin/bans.html", symbol: "float-ban", right: "bans" },
    { key: "admin", label: "Кабінет адміна", href: "/pages/admin/cabinet.html", symbol: "float-admin", admin: true },
  ];

  function hasAdminAccess(user, rights) {
    return rights.length > 0 || !!user.is_admin_1 || !!user.is_admin_main;
  }

  function visibleEntries(user) {
    if (!user || !user.authenticated || !user.policy_accepted) {
      return entries.filter((entry) => entry.public);
    }
    if (user.is_banned) {
      return entries.filter((entry) => ["home", "policy", "support"].includes(entry.key));
    }

    const rights = Array.isArray(user.admin_rights) ? user.admin_rights : [];
    const admin = hasAdminAccess(user, rights);
    const available = entries.filter((entry) => {
      if (entry.auth) return true;
      if (entry.right) return rights.includes(entry.right);
      if (entry.admin) return admin;
      return entry.public;
    });
    if (admin) {
      const standard = available.filter((entry) => entry.key !== "telegram");
      return standard.slice(0, 12);
    }
    return available.slice(0, 12);
  }

  function createIcon(entry, index) {
    const link = document.createElement("a");
    link.className = "floating-icon-link is-entering";
    link.href = entry.href;
    link.dataset.icon = entry.key;
    link.dataset.side = index % 2 === 0 ? "left" : "right";
    link.dataset.depth = String(1 - (index % 4) * 0.17);
    link.setAttribute("aria-label", entry.label);
    link.setAttribute("title", entry.label);
    link.style.setProperty("--entry-delay", Math.floor(index / 2) * 80 + "ms");
    link.style.setProperty("--float-duration", 8.2 + (index % 5) * 0.6 + "s");
    link.style.setProperty("--icon-tilt", ((index % 5) - 2) * 2 + "deg");
    if (entry.key === "home") {
      link.classList.add("is-current");
      link.setAttribute("aria-current", "page");
    }

    const orbit = document.createElement("span");
    orbit.className = "floating-icon-orbit";
    orbit.setAttribute("aria-hidden", "true");
    const hover = document.createElement("span");
    hover.className = "floating-icon-hover";
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.classList.add("floating-icon-art");
    icon.setAttribute("viewBox", "0 0 128 128");
    icon.setAttribute("focusable", "false");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", SPRITE + "#" + entry.symbol);
    icon.appendChild(use);
    hover.appendChild(icon);
    orbit.appendChild(hover);
    link.appendChild(orbit);

    if (entry.key === "cart") {
      const coin = document.createElement("span");
      coin.className = "floating-cart-coin";
      coin.setAttribute("aria-hidden", "true");
      link.appendChild(coin);
    }
    if (entry.counter) {
      const badge = document.createElement("span");
      badge.className = "floating-icon-badge";
      badge.dataset.floatingCounter = entry.counter;
      badge.hidden = true;
      badge.setAttribute("aria-hidden", "true");
      link.appendChild(badge);
    }
    const startFloating = () => {
      link.classList.remove("is-entering");
      link.classList.add("is-floating");
    };
    window.setTimeout(startFloating, 440 + Math.floor(index / 2) * 80);
    return link;
  }

  function updateCounter(entry, source) {
    const count = Number(source.textContent.replace(/\D/g, "")) || 0;
    const badge = document.querySelector('[data-icon="' + entry.key + '"] [data-floating-counter]');
    if (!badge) return;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count <= 0;
    badge.classList.toggle("is-visible", count > 0);
    const link = badge.closest(".floating-icon-link");
    if (link && entry.key === "cart") link.classList.toggle("has-cart-items", count > 0);
  }

  function syncCounters(visible) {
    visible.forEach((entry) => {
      if (!entry.counter) return;
      const selector = entry.counter === "cart"
        ? "[data-cart-count]"
        : '[data-counter="' + entry.counter + '"]';
      const source = document.querySelector(selector);
      if (!source) return;
      updateCounter(entry, source);
      if ("MutationObserver" in window) {
        new MutationObserver(() => updateCounter(entry, source)).observe(source, {
          childList: true,
          characterData: true,
          subtree: true,
        });
      }
    });
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function layoutIcons(links) {
    const main = document.querySelector("main.app-main, main.container");
    if (!main) return;
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = window.innerHeight;
    const bounds = main.getBoundingClientRect();
    const sideSpace = Math.min(bounds.left - 48, viewportWidth - bounds.right - 48);
    if (sideSpace < 56 || viewportWidth < 1100) {
      links.forEach((link) => { link.hidden = true; });
      return;
    }

    const heightSize = Math.max(96, (viewportHeight - 48) / 8.8);
    const size = Math.floor(Math.min(144, heightSize, sideSpace * 0.78));
    if (size < 48) {
      links.forEach((link) => { link.hidden = true; });
      return;
    }
    const drift = Math.min(7, size * 0.075);
    const horizontalPadding = Math.ceil(drift * 1.5);
    const verticalPadding = Math.ceil(drift * 1.6);
    const minStep = size * 1.3;
    const usableHeight = viewportHeight - 48 - size - verticalPadding * 2;
    const capacity = Math.max(1, Math.floor(usableHeight / minStep) + 1);
    const maxPerSide = viewportWidth < 1400 ? Math.min(3, capacity) : Math.min(6, capacity);

    ["left", "right"].forEach((side) => {
      const sideLinks = links.filter((link) => link.dataset.side === side);
      const slots = sideLinks.slice(0, maxPerSide);
      sideLinks.forEach((link, index) => {
        link.hidden = index >= maxPerSide;
      });
      const step = slots.length > 1 ? minStep : 0;
      const span = step * Math.max(0, slots.length - 1);
      const phase = side === "right" && slots.length > 1 ? step / 2 : 0;
      const minTop = 24 + verticalPadding;
      const maxTop = viewportHeight - 24 - size - verticalPadding;
      const preferredTop = (viewportHeight - size - span) / 2 + phase;
      const startTop = Math.round(clamp(preferredTop, minTop, Math.max(minTop, maxTop - span)));

      slots.forEach((link, index) => {
        const x = side === "left"
          ? Math.round(bounds.left - 24 - size - horizontalPadding)
          : Math.round(bounds.right + 24 + horizontalPadding);
        const top = Math.round(clamp(startTop + index * step, minTop, maxTop));
        link.hidden = false;
        link.dataset.motionLimit = String(drift);
        link.style.setProperty("--icon-size", size + "px");
        const floatX = Math.round(drift * 0.45);
        const floatY = Math.round(drift * 0.6);
        link.style.setProperty("--float-x", floatX + "px");
        link.style.setProperty("--float-x-negative", -floatX + "px");
        link.style.setProperty("--float-y", floatY + "px");
        link.style.setProperty("--float-y-negative", -floatY + "px");
        link.style.left = x + "px";
        link.style.right = "auto";
        link.style.top = top + "px";
      });
    });
  }

  function setupParallax(container, links) {
    const parallaxEnabled = !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
      window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    let frame = 0;
    const motion = new Map(links.map((link) => [link, { x: 0, y: 0, targetX: 0, targetY: 0 }]));
    document.addEventListener("visibilitychange", () => {
      container.classList.toggle("is-paused", document.hidden);
      if (document.hidden && frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      } else if (parallaxEnabled) {
        schedule();
      }
    });
    if (!parallaxEnabled) return;

    const draw = () => {
      frame = 0;
      if (document.hidden) return;
      let moving = false;
      links.forEach((link) => {
        if (link.hidden) return;
        const state = motion.get(link);
        state.x += (state.targetX - state.x) * 0.1;
        state.y += (state.targetY - state.y) * 0.1;
        const x = Math.round(state.x);
        const y = Math.round(state.y);
        link.style.setProperty("--parallax-x", x + "px");
        link.style.setProperty("--parallax-y", y + "px");
        if (Math.abs(state.targetX - state.x) > 0.15 || Math.abs(state.targetY - state.y) > 0.15) {
          moving = true;
        }
      });
      if (moving) frame = window.requestAnimationFrame(draw);
    };
    const schedule = () => {
      if (!frame && !document.hidden) frame = window.requestAnimationFrame(draw);
    };

    window.addEventListener("pointermove", (event) => {
      const target = event.target instanceof Element
        ? event.target.closest(".floating-icon-link")
        : null;
      const activeLink = target && links.includes(target) && !target.hidden ? target : null;
      links.forEach((link) => {
        const state = motion.get(link);
        if (link !== activeLink) {
          state.targetX = 0;
          state.targetY = 0;
          return;
        }
        const bounds = link.getBoundingClientRect();
        state.targetX = Math.round(clamp(((event.clientX - bounds.left) / bounds.width - 0.5) * -12, -6, 6));
        state.targetY = Math.round(clamp(((event.clientY - bounds.top) / bounds.height - 0.5) * -12, -6, 6));
      });
      schedule();
    }, { passive: true });
    window.addEventListener("pointerleave", () => {
      motion.forEach((state) => {
        state.targetX = 0;
        state.targetY = 0;
      });
      schedule();
    }, { passive: true });
  }

  async function init() {
    if (!document.querySelector(".hero-panel") || !window.MSAuth) return;
    let user = null;
    try {
      user = await window.MSAuth.refresh({ strict: true });
    } catch (error) {
      console.error("Не вдалося перевірити права для швидких посилань.", error);
    }
    const container = document.createElement("div");
    container.className = "floating-icons";
    container.setAttribute("role", "navigation");
    container.setAttribute("aria-label", "Швидкий перехід між сторінками");
    const visible = visibleEntries(user);
    const links = visible.map(createIcon);
    links.forEach((link) => container.appendChild(link));
    document.body.appendChild(container);

    syncCounters(visible);
    layoutIcons(links);
    let layoutFrame = 0;
    window.addEventListener("resize", () => {
      if (layoutFrame) return;
      layoutFrame = window.requestAnimationFrame(() => {
        layoutFrame = 0;
        layoutIcons(links);
      });
    }, { passive: true });
    setupParallax(container, links);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
