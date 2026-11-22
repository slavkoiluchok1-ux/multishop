/* MultiShop — complaint-tree.js
   Спільний вибір типу скарги: кнопки видів → плитки типів → повноекранне вікно підтипів.
   Це <dialog>, тож вікно коректно лягає поверх інших модальних вікон (замовлення).
   Дані: GET /api/complaint-types?view=…&roots=1 | ?parent=<id>. Використовує MS (api.js).

   Використання:
     window.MSComplaintTree.open({ views: ["orders"], onSelect: (type) => { … } });
   onSelect отримує { id, name, path } вибраного листка. */
(function () {
  "use strict";

  const VIEWS = [
    { id: "orders", label: "На замовлення" },
    { id: "products", label: "На товари" },
    { id: "users_content", label: "На користувачів і вміст" },
  ];

  // Звуження гілок дерева під конкретний об'єкт скарги (розділ «На користувачів і вміст»).
  const SCOPES = {
    comment: ["Коментарі до товару"],
    user: ["Продавець", "Покупець (скарги від продавців)", "Профіль", "Адміністратори"],
  };

  const state = {
    views: [],
    view: "orders",
    scope: null, // ключ зі SCOPES або null (усі типи виду)
    crumbs: [], // пройдений шлях усередині виду: [{ id, name }]
    tiles: [],
    onSelect: null,
    ready: false,
  };

  let win = null;
  let elTitle = null;
  let elCrumbs = null;
  let elViews = null;
  let elBackRow = null;
  let elTiles = null;

  function esc(value) {
    return window.MS.escapeHtml(value);
  }

  function viewLabel(id) {
    const found = VIEWS.find((item) => item.id === id);
    return found ? found.label : id;
  }

  /* ---------- Розмітка вікна ---------- */

  function build() {
    win = document.createElement("dialog");
    win.className = "tree-window";
    win.innerHTML =
      '<div class="tree-window-box">' +
        '<div class="tree-window-head">' +
          "<div>" +
            '<p class="eyebrow mb-1">Тип скарги</p>' +
            '<h2 class="section-title mb-1" id="ctTitle">Оберіть тип скарги</h2>' +
          "</div>" +
          '<button class="app-modal-close" type="button" id="ctClose" aria-label="Закрити">×</button>' +
        "</div>" +
        '<div class="mb-2" id="ctBackRow" hidden>' +
          '<button class="btn btn-outline-light btn-sm" type="button" id="ctBack">← Назад</button>' +
        "</div>" +
        '<nav class="crumbs mb-3" id="ctCrumbs"></nav>' +
        '<div class="view-tabs mb-3" id="ctViews"></div>' +
        '<div class="tile-grid" id="ctTiles"></div>' +
      "</div>";
    document.body.appendChild(win);

    elTitle = win.querySelector("#ctTitle");
    elCrumbs = win.querySelector("#ctCrumbs");
    elViews = win.querySelector("#ctViews");
    elBackRow = win.querySelector("#ctBackRow");
    elTiles = win.querySelector("#ctTiles");

    win.querySelector("#ctClose").addEventListener("click", close);
    win.querySelector("#ctBack").addEventListener("click", goBack);
    win.addEventListener("click", (event) => {
      if (event.target === win) close(); // клік по фону поза вікном
    });
    elViews.addEventListener("click", onViewClick);
    elTiles.addEventListener("click", onTileClick);
    win.addEventListener("close", () => {
      state.crumbs = [];
      state.tiles = [];
    });
  }

  /* ---------- Рендер ---------- */

  function renderCrumbs() {
    elCrumbs.innerHTML = "";
    if (!state.crumbs.length) {
      elCrumbs.hidden = true;
      return;
    }
    elCrumbs.hidden = false;
    const items = [{ id: null, name: viewLabel(state.view) }].concat(state.crumbs);
    items.forEach((crumb, index) => {
      if (index) {
        const sep = document.createElement("span");
        sep.className = "crumbs-sep";
        sep.textContent = "›";
        elCrumbs.appendChild(sep);
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = crumb.name;
      btn.addEventListener("click", () => {
        state.crumbs = state.crumbs.slice(0, index); // клік по рівню повертає на нього
        render();
      });
      elCrumbs.appendChild(btn);
    });
  }

  function renderViews() {
    elViews.innerHTML = state.views.map((id) =>
      '<button type="button" class="view-tab' + (id === state.view ? " is-active" : "") +
        '" data-view="' + esc(id) + '">' + esc(viewLabel(id)) + "</button>"
    ).join("");
  }

  function renderTiles(tiles) {
    if (!tiles.length) {
      elTiles.innerHTML = '<p class="mb-0" style="color:#cfd6dd">Типів не знайдено.</p>';
      return;
    }
    elTiles.innerHTML = tiles.map((tile) =>
      '<button type="button" class="tile" data-id="' + esc(tile.id) + '">' +
        '<span class="tile-name">' + esc(tile.name) + "</span>" +
        (tile.has_children
          ? '<span class="tile-count">Далі: ' + esc(tile.children_count) + "</span>"
          : "") +
      "</button>"
    ).join("");
  }

  /* Звужуємо кореневі гілки під об'єкт скарги; якщо нічого не збіглось — показуємо все. */
  function scopedTiles(tiles, parentId) {
    const allowed = state.scope ? SCOPES[state.scope] : null;
    if (!allowed || parentId) return tiles;
    const filtered = tiles.filter((tile) => allowed.indexOf(tile.name) !== -1);
    return filtered.length ? filtered : tiles;
  }

  async function render() {
    elTitle.textContent = state.crumbs.length
      ? state.crumbs[state.crumbs.length - 1].name
      : "Оберіть тип скарги";
    elBackRow.hidden = state.crumbs.length === 0;
    elViews.hidden = state.crumbs.length > 0;
    renderCrumbs();
    renderViews();
    const parentId = state.crumbs.length ? state.crumbs[state.crumbs.length - 1].id : null;
    const cacheKey = window.MSSkeletons.cacheKey(
      "/complaint-types:" + state.view + ":" + String(parentId || "roots")
    );
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    if (cached !== undefined) {
      state.tiles = scopedTiles(cached, parentId);
      renderTiles(state.tiles);
    }
    const loading = cached === undefined
      ? window.MSSkeletons.showSkeleton(elTiles, "tile", 4)
      : null;
    try {
      const types = await loadTypes(state.view, parentId);
      window.MSSkeletons.setCachedData(cacheKey, types);
      if (loading) {
        await loading.finish(() => {
          state.tiles = scopedTiles(types, parentId);
          renderTiles(state.tiles);
        });
      } else {
        state.tiles = scopedTiles(types, parentId);
        renderTiles(state.tiles);
      }
    } catch (err) {
      state.tiles = [];
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити типи скарг.");
      if (loading) {
        await loading.finish(() =>
          window.MSSkeletons.renderRetryError(elTiles, message, render)
        );
      } else {
        window.MSSkeletons.renderRetryError(elTiles, message, render, true);
      }
    }
  }

  async function loadTypes(view, parentId) {
    const query = new URLSearchParams();
    query.set("view", view);
    if (parentId) query.set("parent", String(parentId));
    else query.set("roots", "1");
    const data = await window.MS.get("/complaint-types?" + query.toString());
    return data.types || [];
  }

  /* ---------- Події ---------- */

  function onViewClick(event) {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    state.view = button.getAttribute("data-view");
    state.crumbs = [];
    render();
  }

  function onTileClick(event) {
    const button = event.target.closest("[data-id]");
    if (!button) return;
    const tile = state.tiles.find(
      (item) => String(item.id) === button.getAttribute("data-id")
    );
    if (!tile) return;
    if (tile.has_children) {
      state.crumbs.push({ id: tile.id, name: tile.name }); // гілка — заглиблюємось
      render();
      return;
    }
    if (state.onSelect) state.onSelect({
      id: tile.id,
      name: tile.name,
      path: tile.path,
      required_fields: tile.required_fields || [],
    });
    close(); // листок — обираємо і закриваємо
  }

  function goBack() {
    if (!state.crumbs.length) return;
    state.crumbs.pop();
    render();
  }

  /* ---------- Спільний блок вибору типу (для форм скарги) ---------- */

  function pickerPathHtml(path) {
    const parts = String(path || "")
      .split("›")
      .map((part) => part.trim())
      .filter((part) => part !== "");
    return parts.map((part, index) =>
      (index ? '<span class="type-picker-sep">›</span>' : "") +
      '<span class="type-picker-part' + (index === parts.length - 1 ? " is-leaf" : "") + '">' +
        esc(part) + "</span>"
    ).join("");
  }

  /* renderPicker(container, { views, scope, onChange }) → { getValue, setValue, setViews, setScope, error }
     Показує «Тип скарги не обрано» + кнопку, а після вибору — помітну картку з повним шляхом. */
  function renderPicker(container, options) {
    options = options || {};
    const el = typeof container === "string" ? document.querySelector(container) : container;
    if (!el) return null;
    const views = Array.isArray(options.views) && options.views.length
      ? options.views.slice()
      : VIEWS.map((item) => item.id);
    let scope = options.scope || null;
    let value = null;

    function paint() {
      el.classList.add("type-picker");
      el.classList.toggle("is-set", !!value);
      el.classList.toggle("is-empty", !value);
      if (value) {
        el.innerHTML =
          '<div class="type-picker-body">' +
            '<p class="type-picker-path">' + pickerPathHtml(value.path || value.name) + "</p>" +
          "</div>" +
          '<button type="button" class="btn btn-outline-primary btn-sm" data-picker-change>Змінити</button>';
        el.querySelector("[data-picker-change]").addEventListener("click", openTree);
      } else {
        el.innerHTML =
          '<p class="type-picker-empty">Тип скарги не обрано</p>' +
          '<button type="button" class="btn btn-outline-primary btn-sm" data-picker-open>' +
            "Обрати тип із дерева…</button>";
        el.querySelector("[data-picker-open]").addEventListener("click", openTree);
      }
    }

    function openTree() {
      open({
        views: views,
        scope: scope,
        onSelect: (type) => {
          el.classList.remove("is-invalid");
          value = type;
          paint();
          if (options.onChange) options.onChange(type);
        },
      });
    }

    paint();

    return {
      getValue: () => value,
      setValue: (type) => {
        value = type || null;
        el.classList.remove("is-invalid");
        paint();
      },
      setViews: (list) => {
        views.length = 0;
        (list || []).forEach((id) => views.push(id));
      },
      setScope: (key) => {
        const next = key || null;
        if (next === scope) return;
        scope = next;
        value = null; // обраний тип міг належати іншому об'єкту — скидаємо
        paint();
      },
      error: () => {
        el.classList.add("is-invalid");
        if (el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
      },
    };
  }

  /* ---------- Публічне API ---------- */

  function open(options) {
    options = options || {};
    if (!state.ready) {
      build();
      state.ready = true;
    }
    const requested =
      Array.isArray(options.views) && options.views.length
        ? options.views
        : VIEWS.map((item) => item.id);
    state.views = requested.filter((id) => VIEWS.some((item) => item.id === id));
    if (!state.views.length) state.views = VIEWS.map((item) => item.id);
    state.view = state.views[0];
    state.scope = options.scope || null;
    state.crumbs = [];
    state.tiles = [];
    state.onSelect = options.onSelect || null;
    if (!win.open) win.showModal(); // Esc закриває лише верхнє вікно
    render();
  }

  function close() {
    if (win && win.open) win.close();
  }

  window.MSComplaintTree = { open: open, close: close, renderPicker: renderPicker };
})();
