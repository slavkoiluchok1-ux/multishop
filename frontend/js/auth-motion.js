/* Анімації авторизації — незалежні від запитів і стану акаунта. */
(function () {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function prepare(container) {
    if (!container) return;
    container.classList.add("auth-motion-ready");
    container.querySelectorAll(".auth-motion-item").forEach((item, index) => {
      item.style.setProperty("--motion-index", String(index));
    });
  }

  function loading(button, isLoading) {
    if (!button) return;
    if (isLoading) {
      if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent.trim();
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.classList.add("auth-loading");
      return;
    }
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.classList.remove("auth-loading");
    if (button.dataset.defaultLabel) button.textContent = button.dataset.defaultLabel;
  }

  function markInvalid(input) {
    if (input) {
      input.classList.add("is-invalid");
      input.setAttribute("aria-invalid", "true");
    }
  }

  function clearInvalid(container) {
    if (!container) return;
    container.querySelectorAll(".is-invalid").forEach((input) => {
      input.classList.remove("is-invalid");
      input.removeAttribute("aria-invalid");
    });
    container.classList.remove("auth-shake");
  }

  function shake(container, inputs) {
    if (container && !reducedMotion.matches) {
      container.classList.remove("auth-shake");
      container.getBoundingClientRect();
      container.classList.add("auth-shake");
    }
    (inputs || []).forEach(markInvalid);
  }

  function addCoins(container) {
    const confetti = container.querySelector(".auth-confetti");
    if (!confetti) return;
    confetti.replaceChildren();
    for (let i = 0; i < 22; i += 1) {
      const coin = document.createElement("i");
      coin.textContent = "MC";
      coin.style.setProperty("--coin-left", (3 + ((i * 41) % 94)) + "%");
      coin.style.setProperty("--coin-delay", (i % 7) * 0.025 + "s");
      coin.style.setProperty("--coin-drift", (i % 2 ? 72 : -72) + "px");
      coin.style.setProperty("--coin-rise", (i % 3 ? -110 : -72) + "px");
      confetti.appendChild(coin);
    }
  }

  function showSuccess(overlay) {
    if (!overlay) return Promise.resolve();
    addCoins(overlay);
    overlay.hidden = false;
    overlay.classList.add("is-visible");
    const duration = reducedMotion.matches ? 320 : 1550;
    return new Promise((resolve) => {
      window.setTimeout(() => {
        overlay.classList.remove("is-visible");
        overlay.classList.add("is-closing");
        window.setTimeout(() => {
          overlay.hidden = true;
          overlay.classList.remove("is-closing");
          resolve();
        }, reducedMotion.matches ? 120 : 200);
      }, duration);
    });
  }

  window.MSAuthMotion = { prepare, loading, markInvalid, clearInvalid, shake, showSuccess };
})();
