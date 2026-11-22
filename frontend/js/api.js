/* MultiShop — api.js
   Базовий клієнт JSON API: сесійні cookie + CSRF, обробка помилок, toast. */
(function () {
  "use strict";

  const API_BASE = "/api";

  function getCookie(name) {
    const target = name + "=";
    const parts = document.cookie ? document.cookie.split(";") : [];
    for (let i = 0; i < parts.length; i += 1) {
      const c = parts[i].trim();
      if (c.indexOf(target) === 0) {
        return decodeURIComponent(c.substring(target.length));
      }
    }
    return null;
  }

  function csrfToken() {
    return getCookie("csrftoken");
  }

  async function ensureCsrf() {
    if (csrfToken()) return csrfToken();
    try {
      await fetch(API_BASE + "/auth/csrf", { credentials: "same-origin" });
    } catch (e) {
      /* мережа недоступна — токен отримаємо пізніше */
    }
    return csrfToken();
  }

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload || {};
    }
  }

  function firstErrorMessage(value) {
    if (Array.isArray(value)) {
      return value.length ? String(value[0]) : "";
    }
    if (typeof value === "string") return value;
    if (value && typeof value === "object") {
      const keys = Object.keys(value);
      return keys.length ? firstErrorMessage(value[keys[0]]) : "";
    }
    return "";
  }

  function messageFromPayload(payload, status) {
    if (status >= 500 || !payload || typeof payload !== "object") {
      return "Щось пішло не так, спробуйте пізніше";
    }
    // Конкретне повідомлення поля важливіше за загальне «Невірні дані».
    const fields = payload.fields && typeof payload.fields === "object" ? payload.fields : payload;
    const keys = Object.keys(fields);
    if (keys.length) {
      const message = firstErrorMessage(fields[keys[0]]);
      if (message) return message;
    }
    if (payload.error) return payload.error;
    if (payload.detail) return payload.detail;
    return "Помилка запиту (" + status + ")";
  }

  async function request(path, options) {
    const opts = options || {};
    // Шляхи виду "/policy" або "policy" — це шляхи API, їх треба префіксувати "/api".
    // Абсолютні URL (http://, https://) використовуємо як є.
    const url = path.startsWith("http") ? path : API_BASE + (path.startsWith("/") ? path : "/" + path);
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});

    let body = opts.body;
    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
    if (body !== undefined && body !== null && !isFormData && typeof body !== "string") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }

    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
      const token = await ensureCsrf();
      if (token) headers["X-CSRFToken"] = token;
    }

    let response;
    try {
      response = await fetch(url, {
        method: method,
        credentials: "same-origin",
        headers: headers,
        body: body,
        signal: opts.signal,
      });
    } catch (_error) {
      throw new ApiError("Щось пішло не так, спробуйте пізніше", 0, {});
    }

    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.indexOf("application/json") !== -1) {
      payload = await response.json().catch(() => null);
    } else {
      await response.text().catch(() => null);
    }

    if (contentType.indexOf("application/json") === -1 || payload === null) {
      throw new ApiError("Щось пішло не так, спробуйте пізніше", response.status, {});
    }

    if (!response.ok) {
      throw new ApiError(messageFromPayload(payload, response.status), response.status, payload);
    }
    return payload;
  }

  /* ---------- toast ---------- */
  function toastStack() {
    let stack = document.querySelector(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, type) {
    const el = document.createElement("div");
    el.className = "app-toast" + (type ? " is-" + type : "");
    el.textContent = message;
    toastStack().appendChild(el);
    window.setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      window.setTimeout(() => el.remove(), 260);
    }, 3800);
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  window.MS = {
    API_BASE: API_BASE,
    ApiError: ApiError,
    getCookie: getCookie,
    csrfToken: csrfToken,
    ensureCsrf: ensureCsrf,
    request: request,
    get: (path, opts) => request(path, Object.assign({ method: "GET" }, opts)),
    post: (path, body, opts) => request(path, Object.assign({ method: "POST", body: body }, opts)),
    put: (path, body, opts) => request(path, Object.assign({ method: "PUT", body: body }, opts)),
    del: (path, opts) => request(path, Object.assign({ method: "DELETE" }, opts)),
    toast: toast,
    escapeHtml: escapeHtml,
  };
})();
