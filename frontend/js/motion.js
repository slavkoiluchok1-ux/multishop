/* Спільний легкий шар руху для статичних сторінок MultiShop. */
(function () {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const revealSelector =
    "[data-reveal], .card-soft, .product-card, .tile, .seller-product-card, .seller-variant-card, " +
    ".cart-row, .wallet-metric, .wallet-table tbody tr, .order-info-card, .order-comment, " +
    ".activation-code-order, .support-message, .support-bubble, .message-bubble, " +
    ".section-heading-panel, .empty-state-panel, .hero-panel";
  const countRafs = new WeakMap();
  const pendingStaggers = new Set();
  const skeletonRuns = new WeakMap();
  const skeletonStats = { shown: 0 };
  const SKELETON_DELAY = 280;
  const SKELETON_MIN_DURATION = 300;
  const SKELETON_CACHE_PREFIX = "ms_skeleton_cache_v1:";
  const pageLinkSelector = "a[href]";
  let revealObserver = null;
  let navResizeObserver = null;
  let mutationObserver = null;
  let staggerFrame = 0;

  function isElement(value) {
    return value instanceof Element;
  }

  function skeletonCard(kind) {
    const card = document.createElement("div");
    card.className = "skeleton-card skeleton-card--" + kind;
    if (kind === "home") card.classList.add("col-12", "col-md-6", "col-lg-4");
    card.setAttribute("aria-hidden", "true");

    if (kind === "product") {
      card.innerHTML =
        '<div class="skeleton skeleton-product-thumb">' +
          '<span class="skeleton skeleton-price-badge"></span>' +
          '<span class="skeleton skeleton-heart"></span>' +
        "</div>" +
        '<div class="skeleton-product-body">' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-title is-short"></span>' +
          '<span class="skeleton skeleton-type-badge"></span>' +
          '<div class="skeleton-product-foot">' +
            '<span class="skeleton skeleton-line is-seller"></span>' +
            '<span class="skeleton skeleton-button"></span>' +
          "</div>" +
        "</div>";
    } else if (kind === "cart") {
      card.innerHTML =
        '<span class="skeleton skeleton-cart-thumb"></span>' +
        '<span class="skeleton-cart-info">' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-short"></span>' +
          '<span class="skeleton skeleton-line is-note"></span>' +
        "</span>" +
        '<span class="skeleton-cart-controls">' +
          '<span class="skeleton skeleton-quantity"></span>' +
          '<span class="skeleton skeleton-line is-price"></span>' +
          '<span class="skeleton skeleton-button"></span>' +
        "</span>";
    } else if (kind === "seller") {
      card.innerHTML =
        '<span class="skeleton skeleton-seller-thumb"></span>' +
        '<span class="skeleton-seller-body">' +
          '<span class="skeleton skeleton-line is-short"></span>' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-title is-short"></span>' +
          '<span class="skeleton skeleton-type-badge"></span>' +
          '<span class="skeleton skeleton-line is-wide"></span>' +
        "</span>";
    } else if (kind === "tile") {
      card.innerHTML =
        '<span class="skeleton skeleton-line is-title"></span>' +
        '<span class="skeleton skeleton-line is-short"></span>';
    } else if (kind === "home") {
      card.innerHTML =
        '<span class="skeleton skeleton-line is-title"></span>' +
        '<span class="skeleton skeleton-line is-wide"></span>' +
        '<span class="skeleton skeleton-line is-short"></span>';
    } else if (kind === "profile") {
      card.innerHTML =
        '<span class="skeleton skeleton-line is-wide"></span>' +
        '<span class="skeleton skeleton-line is-short"></span>';
    } else if (kind === "policy") {
      card.innerHTML =
        '<span class="skeleton skeleton-line is-title"></span>' +
        '<span class="skeleton skeleton-line is-wide"></span>' +
        '<span class="skeleton skeleton-line is-title"></span>' +
        '<span class="skeleton skeleton-line is-wide"></span>' +
        '<span class="skeleton skeleton-line is-wide"></span>' +
        '<span class="skeleton skeleton-line is-short"></span>';
    } else if (kind === "product-detail") {
      card.innerHTML =
        '<span class="skeleton skeleton-detail-image"></span>' +
        '<span class="skeleton-detail-body">' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-wide"></span>' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-short"></span>' +
        "</span>";
    } else {
      card.innerHTML =
        '<span class="skeleton-row-main">' +
          '<span class="skeleton skeleton-line is-title"></span>' +
          '<span class="skeleton skeleton-line is-wide"></span>' +
          '<span class="skeleton skeleton-line is-short"></span>' +
        "</span>" +
        '<span class="skeleton-row-side">' +
          '<span class="skeleton skeleton-type-badge"></span>' +
          '<span class="skeleton skeleton-line is-date"></span>' +
        "</span>";
    }
    return card;
  }

  function skeletonDelay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function showSkeleton(container, kind, requestedCount) {
    if (!container) {
      return {
        finish: (render) => {
          if (typeof render === "function") render();
          return Promise.resolve(true);
        },
        cancel: () => {},
      };
    }

    const previous = skeletonRuns.get(container);
    if (previous) previous.cancel();

    const type = kind || "row";
    const count = Math.max(1, Math.min(24, Math.floor(Number(requestedCount) || 1)));
    const originalMinHeight = container.style.minHeight;
    const originalAriaBusy = container.getAttribute("aria-busy");
    const previousHeight = Math.ceil(container.getBoundingClientRect().height);
    const hasExistingContent = Array.from(container.children).some((child) =>
      !child.matches(".skeleton-card, .skeleton, .skeleton-retry-error") &&
      !child.hidden &&
      window.getComputedStyle(child).display !== "none"
    );
    const run = {
      timer: 0,
      visible: false,
      shownAt: 0,
      cancelled: false,
      nodes: [],
      cancel: null,
      finish: null,
    };

    function clean(preserveHeight) {
      if (run.timer) window.clearTimeout(run.timer);
      run.nodes.forEach((node) => node.remove());
      container.classList.remove("skeleton-loading", "skeleton-fading");
      if (!preserveHeight) container.style.minHeight = originalMinHeight;
      if (originalAriaBusy === null) container.removeAttribute("aria-busy");
      else container.setAttribute("aria-busy", originalAriaBusy);
      if (skeletonRuns.get(container) === run) skeletonRuns.delete(container);
    }

    run.cancel = () => {
      run.cancelled = true;
      clean();
    };

    run.timer = window.setTimeout(() => {
      if (run.cancelled || skeletonRuns.get(container) !== run) return;
      if (hasExistingContent) return;
      container.classList.add("skeleton-loading");
      container.setAttribute("aria-busy", "true");

      const fragment = document.createDocumentFragment();
      for (let index = 0; index < count; index += 1) {
        const card = skeletonCard(type);
        run.nodes.push(card);
        fragment.appendChild(card);
      }
      container.appendChild(fragment);
      if (previousHeight) {
        container.style.minHeight = Math.max(previousHeight, container.scrollHeight) + "px";
      }
      run.visible = true;
      run.shownAt = performance.now();
      skeletonStats.shown += 1;
      if (window.MS_DEBUG === true) {
        console.debug("[skeleton] показано за перехід:", skeletonStats.shown);
      }
      window.requestAnimationFrame(() => {
        if (run.cancelled || skeletonRuns.get(container) !== run) return;
        run.nodes.forEach((node) => node.classList.add("is-visible"));
      });
    }, SKELETON_DELAY);

    run.finish = async (render) => {
      if (run.cancelled || skeletonRuns.get(container) !== run) return false;
      if (run.timer) {
        window.clearTimeout(run.timer);
        run.timer = 0;
      }
      if (run.visible) {
        const remaining = Math.max(0, SKELETON_MIN_DURATION - (performance.now() - run.shownAt));
        if (remaining) await skeletonDelay(remaining);
        if (run.cancelled || skeletonRuns.get(container) !== run) return false;
        container.classList.add("skeleton-fading");
        await skeletonDelay(reducedMotion.matches ? 80 : 150);
      }
      if (run.cancelled || skeletonRuns.get(container) !== run) return false;
      const preserveHeight = run.visible;
      clean(preserveHeight);
      try {
        if (typeof render === "function") render();
      } finally {
        if (preserveHeight) {
          window.requestAnimationFrame(() => {
            if (!skeletonRuns.has(container)) container.style.minHeight = originalMinHeight;
          });
        }
      }
      return true;
    };

    skeletonRuns.set(container, run);
    return run;
  }

  function cacheKey(resource) {
    const auth = window.MSAuth && window.MSAuth.state;
    const user = auth && auth.user;
    const identity = user && user.authenticated
      ? "user:" + String(user.id || user.username || "unknown")
      : "guest";
    return SKELETON_CACHE_PREFIX + encodeURIComponent(identity) + ":" +
      encodeURIComponent(String(resource));
  }

  function getCachedData(key) {
    try {
      const entry = JSON.parse(window.sessionStorage.getItem(key) || "null");
      return entry && entry.version === 1 ? entry.data : undefined;
    } catch (error) {
      return undefined;
    }
  }

  function setCachedData(key, data) {
    try {
      window.sessionStorage.setItem(key, JSON.stringify({
        version: 1,
        savedAt: Date.now(),
        data: data,
      }));
    } catch (error) {
      if (window.MS_DEBUG === true) {
        console.debug("[skeleton] кеш недоступний:", error);
      }
    }
  }

  function friendlyErrorMessage(error, fallback) {
    const status = Number(error && error.status);
    const message = error && typeof error.message === "string" ? error.message.trim() : "";
    const isHtml = /<\/?(?:!doctype|html|head|body|pre|title|div|table|script)\b/i.test(message);
    if (status >= 400 && status < 500 && message && !isHtml) return message;
    if (status >= 400 && status < 500) return fallback || "Не вдалося завантажити дані.";
    return "Щось пішло не так, спробуйте пізніше.";
  }

  function renderRetryError(container, message, retry, preserveContent) {
    if (!container) return;
    if (preserveContent !== true) container.replaceChildren();
    else {
      container.querySelectorAll(".skeleton-retry-error").forEach((item) => item.remove());
    }

    const hostIsAlert = container.classList.contains("alert-soft");
    const error = document.createElement("div");
    error.className = hostIsAlert
      ? "skeleton-retry-error-inner"
      : "alert-soft is-danger skeleton-retry-error";
    error.setAttribute("role", "alert");
    const text = document.createElement("span");
    text.textContent = message || "Не вдалося завантажити дані.";
    const button = document.createElement("button");
    button.className = "btn btn-outline-danger btn-sm";
    button.type = "button";
    button.textContent = "Спробувати ще раз";
    button.addEventListener("click", () => {
      if (hostIsAlert) {
        container.replaceChildren();
        container.hidden = true;
        container.removeAttribute("role");
      } else {
        error.remove();
      }
      retry();
    });
    if (hostIsAlert) {
      error.append(text, button);
      container.replaceChildren(error);
      container.hidden = false;
      container.setAttribute("role", "alert");
    } else {
      error.append(text, button);
      container.appendChild(error);
    }
  }

  function isSafeInternalUrl(value) {
    try {
      const target = new URL(value, window.location.origin);
      return target.origin === window.location.origin;
    } catch (error) {
      return false;
    }
  }

  function reveal(element) {
    if (element.classList.contains("is-revealed")) return;
    element.classList.add("is-revealed", "motion-revealing");
    window.setTimeout(() => {
      element.classList.remove("motion-revealing");
      element.style.removeProperty("--motion-delay");
    }, 1000);
    animateCount(element);
  }

  function parseCount(element) {
    if (element.dataset.count === undefined && element.classList.contains("stat-value")) {
      const match = element.textContent.match(/^\s*(-?[\d,.]+)(.*)$/);
      if (match) {
        element.dataset.count = match[1].replace(/,/g, "");
        element.dataset.countSuffix = match[2];
      }
    }
    if (element.dataset.count === undefined) return null;

    const target = Number(element.dataset.count);
    if (!Number.isFinite(target)) return null;
    const suffix = element.dataset.countSuffix || "";
    const decimals = Math.max(0, Number(element.dataset.countDecimals || 0));
    return { target: target, suffix: suffix, decimals: decimals };
  }

  function animateCount(element) {
    if (element.dataset.countDone === "true") return;
    const activeFrame = countRafs.get(element);
    if (activeFrame) window.cancelAnimationFrame(activeFrame);
    const count = parseCount(element);
    if (!count) return;
    element.dataset.countDone = "true";
    const finalText = count.target.toFixed(count.decimals) + count.suffix;
    if (reducedMotion.matches) {
      element.textContent = finalText;
      return;
    }

    const start = performance.now();
    const duration = 1050;
    const update = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(2, -10 * progress);
      const current = count.target * eased;
      element.textContent = current.toFixed(count.decimals) + count.suffix;
      if (progress < 1) {
        countRafs.set(element, window.requestAnimationFrame(update));
      } else {
        countRafs.delete(element);
        element.textContent = finalText;
      }
    };
    countRafs.set(element, window.requestAnimationFrame(update));
  }

  function observeReveal(element) {
    if (element.dataset.revealObserved === "true") return;
    element.dataset.revealObserved = "true";
    if (!revealObserver || reducedMotion.matches) {
      reveal(element);
      return;
    }
    revealObserver.observe(element);
  }

  function prepareStagger(container) {
    const children = Array.from(container.children).filter((child) =>
      child.matches(
        ".col, .product-card, .tile, .home-block-card, .seller-product-card, .seller-variant-card, " +
        ".cart-row, .wallet-metric, .order-info-card, .order-comment, " +
        "#myOrders > .card-soft, #incomingOrders > .card-soft, #ticketList > .card-soft, " +
        ".support-message, .support-bubble, .message-bubble, tr, [data-reveal]"
      )
    );
    children.forEach((child, index) => {
      if (child.dataset.motionStaggerReady === "true" || child.classList.contains("is-revealed")) return;
      if (!child.hasAttribute("data-reveal")) child.setAttribute("data-reveal", "");
      child.style.setProperty("--motion-delay", Math.min(index * 55, 600) + "ms");
      child.dataset.motionStaggerReady = "true";
    });
  }

  function scheduleStagger(container) {
    pendingStaggers.add(container);
    if (staggerFrame) return;
    staggerFrame = window.requestAnimationFrame(() => {
      pendingStaggers.forEach(prepareStagger);
      pendingStaggers.clear();
      staggerFrame = 0;
    });
  }

  function setTilt(element) {
    if (element.dataset.tiltReady === "true" || reducedMotion.matches ||
        !window.matchMedia("(hover: hover) and (pointer: fine)").matches ||
        element.querySelector("form, input, textarea, select, [contenteditable='true']")) return;
    element.dataset.tiltReady = "true";
    element.classList.add("motion-tilt");
    element.addEventListener("pointermove", (event) => {
      if (event.pointerType === "touch") return;
      const bounds = element.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      const limit = element.classList.contains("product-card") ? 4 : 3;
      element.style.setProperty("--tilt-x", (-y * limit).toFixed(2) + "deg");
      element.style.setProperty("--tilt-y", (x * limit).toFixed(2) + "deg");
    });
    element.addEventListener("pointerleave", () => {
      element.style.removeProperty("--tilt-x");
      element.style.removeProperty("--tilt-y");
    });
  }

  function discover(root) {
    if (!isElement(root) && root !== document) return;
    const elements = [];
    if (root instanceof Element && root.matches(revealSelector)) {
      elements.push(root);
    }
    elements.push(...root.querySelectorAll(revealSelector));

    elements.forEach((element) => {
      if (
        element.matches("dialog, .auth-card, .auth-recovery-dialog, #homeBlockDialog, #telegramDialog") ||
        element.closest("dialog")
      ) return;
      if (!element.hasAttribute("data-reveal")) element.setAttribute("data-reveal", "");
      observeReveal(element);
    });

    root.querySelectorAll("[data-count]").forEach((element) => {
      if (!element.hasAttribute("data-reveal")) element.setAttribute("data-reveal", "");
      observeReveal(element);
    });

    const staggerSelector =
      "[data-stagger], .product-grid, #homeBlocks, #tileGrid, .seller-product-list, .seller-variants, " +
      ".cart-list, .wallet-metrics, .wallet-table tbody, .order-list, .complaint-list, " +
      "#myOrders, #incomingOrders, #ticketList";
    if (root instanceof Element) {
      if (root.matches(staggerSelector)) scheduleStagger(root);
      const parent = root.closest(staggerSelector);
      if (parent) scheduleStagger(parent);
    }
    root.querySelectorAll(staggerSelector).forEach(scheduleStagger);
    if (root === document) {
      document.querySelectorAll("main > section, main > .row, main > .section-heading-panel").forEach((element) => {
        if (!element.matches(".hero-panel")) {
          if (!element.hasAttribute("data-reveal")) element.setAttribute("data-reveal", "");
          observeReveal(element);
        }
      });
    }
  }

  function initializeNavigationIndicator() {
    const lists = document.querySelectorAll(".navbar .navbar-nav");
    lists.forEach((list) => {
      const active = list.querySelector(".nav-link.active");
      if (!active) return;
      let indicator = list.querySelector(".nav-shared-indicator");
      if (!indicator) {
        indicator = document.createElement("span");
        indicator.className = "nav-shared-indicator";
        indicator.setAttribute("aria-hidden", "true");
        list.prepend(indicator);
      }
      const place = () => {
        if (!active.isConnected || active.offsetParent === null) return;
        const bounds = active.getBoundingClientRect();
        const parent = list.getBoundingClientRect();
        indicator.style.setProperty("--indicator-x", (bounds.left - parent.left) + "px");
        indicator.style.setProperty("--indicator-y", (bounds.top - parent.top) + "px");
        indicator.style.setProperty("--indicator-width", bounds.width + "px");
        indicator.style.setProperty("--indicator-height", bounds.height + "px");
      };
      place();
      if ("ResizeObserver" in window) {
        navResizeObserver = new ResizeObserver(place);
        navResizeObserver.observe(list);
      } else {
        window.addEventListener("resize", place, { passive: true });
      }
      document.addEventListener("shown.bs.collapse", place);
    });
  }

  function observeCounters() {
    const counters = document.querySelectorAll(".nav-counter, [data-cart-count], [data-wallet-balance]");
    if (!("MutationObserver" in window)) return;
    const counterObserver = new MutationObserver((changes) => {
      changes.forEach((change) => {
        const element = change.target.nodeType === Node.TEXT_NODE ? change.target.parentElement : change.target;
        if (!isElement(element) || !element.textContent.trim()) return;
        element.classList.remove("motion-pop", "motion-pulse");
        void element.offsetWidth;
        element.classList.add("motion-pop", "motion-pulse");
      });
    });
    counters.forEach((element) => counterObserver.observe(element, { childList: true, characterData: true, subtree: true }));
  }

  function bindRipple() {
    document.addEventListener("click", (event) => {
      if (!isElement(event.target)) return;
      const button = event.target.closest("button, .btn");
      if (!button || reducedMotion.matches || button.disabled) return;
      const bounds = button.getBoundingClientRect();
      const ripple = document.createElement("span");
      ripple.className = "motion-ripple";
      ripple.style.left = (event.clientX - bounds.left) + "px";
      ripple.style.top = (event.clientY - bounds.top) + "px";
      button.classList.add("motion-ripple-host");
      button.appendChild(ripple);
      ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
    });
  }

  function bindTilt() {
    const interactiveFields = "form, input, textarea, select, [contenteditable='true']";
    document.querySelectorAll(".card-soft").forEach((card) => {
      if (card.querySelector(interactiveFields)) card.classList.add("motion-no-tilt");
    });
    document.addEventListener("pointerover", (event) => {
      if (!isElement(event.target)) return;
      const softCard = event.target.closest(".card-soft");
      if (softCard && softCard.querySelector(interactiveFields)) {
        softCard.classList.add("motion-no-tilt");
      }
      const card = event.target.closest("[data-tilt], .product-card, .tile, .seller-product-card");
      if (card) setTilt(card);
    }, { passive: true });
  }

  function resetPageScroll() {
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
    document.documentElement.style.scrollBehavior = "auto";
    window.scrollTo(0, 0);
  }

  function navigate(target) {
    const url = new URL(target, window.location.origin);
    if (!isSafeInternalUrl(url.href)) {
      window.location.assign(url.href);
      return;
    }
    if (reducedMotion.matches) {
      resetPageScroll();
      window.location.assign(url.href);
      return;
    }
    document.documentElement.classList.add("motion-leaving");
    window.setTimeout(() => {
      resetPageScroll();
      window.location.assign(url.href);
    }, 150);
  }

  function bindPageLinks() {
    document.addEventListener("click", (event) => {
      if (event.defaultPrevented || !isElement(event.target)) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest(pageLinkSelector);
      if (!link || link.target || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (!isSafeInternalUrl(url.href) || url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      event.preventDefault();
      navigate(url.href);
    });
  }

  function setupHome() {
    const hero = document.querySelector(".hero-panel");
    if (!hero) return;
    hero.classList.add("home-hero-ready");
    const title = hero.querySelector("h1");
    if (title && !title.dataset.linesReady) {
      title.dataset.linesReady = "true";
      const source = title.textContent.trim();
      const renderLines = (animate) => {
        const words = source.split(/\s+/);
        title.replaceChildren();
        const measured = words.map((word, index) => {
          const span = document.createElement("span");
          span.className = "hero-title-word";
          span.textContent = word;
          title.append(span);
          if (index < words.length - 1) title.append(document.createTextNode(" "));
          return span;
        });
        const lines = [];
        measured.forEach((word) => {
          const y = word.offsetTop;
          let line = lines.find((entry) => entry.y === y);
          if (!line) {
            line = { y: y, words: [] };
            lines.push(line);
          }
          line.words.push(word.textContent);
        });
        title.replaceChildren();
        title.classList.toggle("home-title-reflowing", !animate);
        lines.forEach((line, index) => {
          const wrapper = document.createElement("span");
          wrapper.className = "hero-title-line";
          wrapper.style.setProperty("--line-index", String(index));
          const inner = document.createElement("span");
          inner.className = "hero-title-line-inner";
          inner.textContent = line.words.join(" ") + (index < lines.length - 1 ? " " : "");
          wrapper.appendChild(inner);
          title.appendChild(wrapper);
        });
        if (!animate) {
          window.requestAnimationFrame(() => title.classList.remove("home-title-reflowing"));
        }
      };
      renderLines(true);
      let resizeTimer = 0;
      window.addEventListener("resize", () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => renderLines(false), 120);
      }, { passive: true });
    }

    const ambient = hero.querySelector(".hero-ambient");
    if (ambient && !ambient.querySelector(".hero-mc-coin")) {
      for (let index = 0; index < 4; index += 1) {
        const coin = document.createElement("span");
        coin.className = "hero-mc-coin";
        coin.textContent = "MC";
        coin.setAttribute("aria-hidden", "true");
        coin.style.setProperty("--coin-index", String(index));
        ambient.appendChild(coin);
      }
    }

    hero.querySelectorAll(".stat-value").forEach((value) => {
      const match = value.textContent.match(/^\s*([\d,.]+)(.*)$/);
      if (match && value.dataset.count === undefined) {
        value.dataset.count = match[1].replace(/,/g, "");
        value.dataset.countSuffix = match[2];
      }
    });

    const paragraph = hero.querySelector(".lead");
    if (paragraph) {
      paragraph.dataset.reveal = "";
      paragraph.style.setProperty("--motion-delay", "280ms");
      observeReveal(paragraph);
    }
    hero.querySelectorAll(".btn").forEach((button, index) => {
      button.dataset.reveal = "";
      button.style.setProperty("--motion-delay", (390 + index * 70) + "ms");
      observeReveal(button);
    });
    hero.querySelectorAll(".stat-value").forEach((stat, index) => {
      stat.dataset.reveal = "";
      stat.style.setProperty("--motion-delay", (500 + index * 60) + "ms");
      observeReveal(stat);
    });
    hero.querySelectorAll(".stat-strip .eyebrow").forEach((label, index) => {
      label.dataset.reveal = "";
      label.style.setProperty("--motion-delay", (470 + index * 60) + "ms");
      observeReveal(label);
    });

  }

  function init() {
    if ("IntersectionObserver" in window && !reducedMotion.matches) {
      revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          observer.unobserve(entry.target);
          reveal(entry.target);
        });
      }, { threshold: 0.12, rootMargin: "0px 0px -24px 0px" });
    }
    document.documentElement.classList.add("motion-ready");
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
    discover(document);
    setupHome();
    initializeNavigationIndicator();
    observeCounters();
    bindRipple();
    bindTilt();
    bindPageLinks();

    mutationObserver = new MutationObserver((changes) => {
      changes.forEach((change) => {
        change.addedNodes.forEach((node) => {
          if (node instanceof Element) discover(node);
        });
      });
    });
    mutationObserver.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && mutationObserver) mutationObserver.disconnect();
      else if (mutationObserver) {
        mutationObserver.observe(document.body, { childList: true, subtree: true });
        discover(document);
      }
    });

    document.documentElement.classList.add("motion-enter");
    window.setTimeout(() => document.documentElement.classList.remove("motion-enter"), 360);
  }

  window.MSMotion = {
    navigate: navigate,
    reveal: reveal,
    count: animateCount,
    discover: discover,
    stagger: scheduleStagger,
    reducedMotion: reducedMotion,
  };
  window.MSSkeletons = {
    showSkeleton: showSkeleton,
    cacheKey: cacheKey,
    getCachedData: getCachedData,
    setCachedData: setCachedData,
    friendlyErrorMessage: friendlyErrorMessage,
    renderRetryError: renderRetryError,
    stats: skeletonStats,
  };
  window.addEventListener("pagehide", () => {
    if (window.MS_DEBUG === true) {
      console.debug("[skeleton] показано за перехід:", skeletonStats.shown);
    }
  });
  window.addEventListener("pageshow", () => {
    skeletonStats.shown = 0;
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
