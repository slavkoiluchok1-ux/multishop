/* MultiShop — policy-gate.js
   Якщо користувач залогінений, але не прийняв поточну політику —
   пропонує перейти на сторінку політики (узгоджено з PolicyGateMiddleware). */
(function () {
  "use strict";

  const POLICY_PAGE = "/pages/policy.html";
  const LOGIN_PAGE = "/pages/login.html";

  function onPolicyPage() {
    return window.location.pathname.replace(/\/+$/, "").endsWith("/policy.html");
  }

  function safeInternalTarget(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
      return null;
    }
    try {
      const target = new URL(value, window.location.origin);
      if (target.origin !== window.location.origin) return null;
      if (target.pathname === LOGIN_PAGE || target.pathname === "/pages/register.html" || target.pathname === POLICY_PAGE) {
        return null;
      }
      return target.pathname + target.search + target.hash;
    } catch (error) {
      return null;
    }
  }

  function redirectToPolicy() {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    const target = POLICY_PAGE + "?required=1&next=" + next;
    if (window.MSMotion) window.MSMotion.navigate(target);
    else window.location.href = target;
  }

  function check(user) {
    if (!user || !user.authenticated) return;
    if (user.policy_accepted) return;
    if (onPolicyPage()) return;
    redirectToPolicy();
  }

  function nextTarget() {
    const params = new URLSearchParams(window.location.search);
    return safeInternalTarget(params.get("next")) || "/";
  }

  async function afterLogin(user) {
    if (!window.MSAuth || typeof window.MSAuth.refresh !== "function") {
      return user && user.authenticated ? POLICY_PAGE + "?required=1" : LOGIN_PAGE;
    }

    user = await window.MSAuth.refresh({ strict: true });
    if (!user || !user.authenticated) return LOGIN_PAGE;
    if (window.MSCounters && typeof window.MSCounters.refresh === "function") {
      await window.MSCounters.refresh();
    }
    if (user.policy_accepted !== true) return POLICY_PAGE + "?required=1";
    return nextTarget();
  }

  function attachNextToLoginLinks() {
    document.addEventListener("click", (event) => {
      if (!event.target || typeof event.target.closest !== "function") return;
      const link = event.target.closest("a[href]");
      if (!link || window.location.pathname === LOGIN_PAGE) return;
      const target = new URL(link.href, window.location.origin);
      if (target.origin !== window.location.origin || target.pathname !== LOGIN_PAGE || target.searchParams.has("next")) {
        return;
      }
      target.searchParams.set(
        "next",
        window.location.pathname + window.location.search + window.location.hash
      );
      link.href = target.pathname + target.search + target.hash;
    });
  }

  async function accept() {
    const acceptance = await window.MS.post("/policy/accept");
    const user = await window.MSAuth.refresh({ strict: true });
    if (window.MSCounters) await window.MSCounters.refresh();
    if (!user || !user.authenticated || !user.policy_accepted) {
      throw new Error("Не вдалося підтвердити прийняття політики.");
    }
    return { target: "/", acceptedAt: acceptance.accepted_at };
  }

  document.addEventListener("DOMContentLoaded", () => {
    attachNextToLoginLinks();
    if (!window.MSAuth) return;
    window.MSAuth.ready().then(check);
  });

  window.MSPolicyGate = {
    POLICY_PAGE: POLICY_PAGE,
    onPolicyPage: onPolicyPage,
    safeInternalTarget: safeInternalTarget,
    nextTarget: nextTarget,
    afterLogin: afterLogin,
    accept: accept,
  };
})();
