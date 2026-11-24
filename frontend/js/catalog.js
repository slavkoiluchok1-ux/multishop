/* MultiShop — catalog.js
   Каталог: три кнопки виду, дерево розділів (можна вибрати кілька вузлів),
   фільтри за характеристиками, пошук, сортування, пагінація, стан у URL.
   Використовує MS (api.js), MSTree (tree-popup.js). */
(function () {
  "use strict";

  const state = {
    view: "",
    categories: [],
    q: "",
    sort: "time",
    availability: "active",
    page: 1,
    attrs: {},   // { attributeId: ["value", ...] }, порожній рядок = «не вказано»
    meta: {},    // { categoryId: { id, name, path, crumbs } }
  };
  let allViews = [];

  const SCROLL_PREFIX = "ms_catalog_scroll:";       // позиція прокрутки для URL
  const LAST_CATALOG_URL_KEY = "ms_last_catalog_url";
  const PRODUCTS_CACHE_PREFIX = "ms_catalog_cache:"; // кеш відповіді /api/products
  const PRODUCTS_CACHE_TTL = 30 * 1000;
  const PRODUCT_PAGE_SIZE = 24;
  let productLoadSequence = 0;
  let activeProductRequest = null;
  let activeProductLoading = null;
  let renderedProductsSignature = null;
  let eventController = null;
  let initialized = false;
  let treeInitialized = false;
  let initSequence = 0;
  const debugStats = { init: 0, fetchProducts: 0, render: 0 };
  let debugStatsLogged = false;

  // null — прокрутку не чіпаємо; число — куди прокрутити після рендера списку
  let scrollTarget = null;

  const els = {};

  function catalogUrlKey() {
    return window.location.pathname + window.location.search;
  }

  function saveScroll() {
    try {
      sessionStorage.setItem(SCROLL_PREFIX + catalogUrlKey(), String(window.scrollY || 0));
    } catch (e) { /* приватний режим — ігноруємо */ }
  }

  function readSavedScroll() {
    try {
      const value = sessionStorage.getItem(SCROLL_PREFIX + catalogUrlKey());
      return value === null ? 0 : Number(value) || 0;
    } catch (e) {
      return 0;
    }
  }

  function rememberCatalogUrl() {
    try {
      sessionStorage.setItem(LAST_CATALOG_URL_KEY, catalogUrlKey());
    } catch (e) { /* ігноруємо */ }
  }

  function applyScroll() {
    if (scrollTarget === null) return;
    const top = scrollTarget;
    scrollTarget = null;
    window.scrollTo({ top: top, behavior: "auto" });
  }

  function readProductsCache(key) {
    try {
      const entry = JSON.parse(sessionStorage.getItem(key) || "null");
      if (!entry || !entry.data || Date.now() - entry.t > PRODUCTS_CACHE_TTL) return null;
      return entry.data;
    } catch (e) {
      return null;
    }
  }

  function writeProductsCache(key, data) {
    try {
      sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), data: data }));
    } catch (e) { /* нема місця — ігноруємо */ }
  }

  function $(id) {
    return document.getElementById(id);
  }

  function viewSuffix() {
    const p = new URLSearchParams();
    if (state.view) p.set("view", state.view);
    p.set("availability", state.availability);
    const qs = p.toString();
    return qs ? "?" + qs : "";
  }

  /* ---------- Стан у URL ---------- */
  function readUrl() {
    const p = new URLSearchParams(window.location.search);
    state.view = p.get("view") || "";
    state.categories = (p.get("category") || "").split(",").filter((x) => /^\d+$/.test(x));
    state.q = p.get("q") || "";
    state.sort = p.get("sort") || "time";
    const availability = p.get("availability");
    state.availability = ["all", "active", "sold_out"].indexOf(availability) !== -1
      ? availability
      : "active";
    state.page = Math.max(1, parseInt(p.get("page") || "1", 10) || 1);
    state.attrs = {};
    p.getAll("attr").forEach((raw) => {
      const sep = raw.indexOf(":");
      if (sep <= 0) return;
      const id = raw.slice(0, sep);
      const value = raw.slice(sep + 1);
      if (!state.attrs[id]) state.attrs[id] = [];
      state.attrs[id].push(value);
    });
  }

  function attrParams() {
    const result = [];
    Object.keys(state.attrs).forEach((id) => {
      (state.attrs[id] || []).forEach((value) => result.push(id + ":" + value));
    });
    return result;
  }

  // useReplace=true — перезаписати поточний запис історії (початкове завантаження,
  // повернення «Назад»); інакше — новий запис, щоб «Назад» вів до попереднього стану.
  function writeUrl(useReplace) {
    const p = new URLSearchParams();
    if (state.view) p.set("view", state.view);
    if (state.categories.length) p.set("category", state.categories.join(","));
    if (state.q) p.set("q", state.q);
    if (state.sort && state.sort !== "time") p.set("sort", state.sort);
    // У URL пишемо лише коли наявність НЕ типова («В наявності» — типове значення)
    if (state.availability !== "active") p.set("availability", state.availability);
    if (state.page > 1) p.set("page", String(state.page));
    attrParams().forEach((pair) => p.append("attr", pair));
    const qs = p.toString();
    const url = qs ? window.location.pathname + "?" + qs : window.location.pathname;
    if (useReplace) window.history.replaceState({}, "", url);
    else window.history.pushState({}, "", url);
  }

  function productsUrl() {
    const p = new URLSearchParams();
    if (state.view) p.set("view", state.view);
    if (state.categories.length) p.set("category", state.categories.join(","));
    if (state.q) p.set("q", state.q);
    if (state.sort) p.set("sort", state.sort);
    p.set("availability", state.availability);
    if (state.page > 1) p.set("page", String(state.page));
    attrParams().forEach((pair) => p.append("attr", pair));
    const qs = p.toString();
    return qs ? "?" + qs : "";
  }

  function productsCacheKey(requestUrl) {
    const auth = window.MSAuth && window.MSAuth.state;
    const user = auth && auth.user;
    const identity = user && user.authenticated
      ? "user:" + String(user.username || user.id || "unknown")
      : "guest";
    return PRODUCTS_CACHE_PREFIX + encodeURIComponent(identity) + ":" + requestUrl;
  }

  /* ---------- Три кнопки виду ---------- */
  async function loadViews() {
    try {
      const data = await window.MS.get("/catalog/views");
      allViews = data.views || [];
      els.viewTabs.innerHTML = "";
      allViews.forEach((view) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "view-tab";
        btn.dataset.view = view.key;
        btn.textContent = view.label;
        btn.addEventListener("click", () => selectView(view.key));
        els.viewTabs.appendChild(btn);
      });
      paintViews();
    } catch (err) {
      window.MS.toast(err.message || "Не вдалося завантажити види товарів.", "danger");
    }
  }

  function paintViews() {
    els.viewTabs.querySelectorAll(".view-tab").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.view === state.view);
    });
  }

  function selectView(key) {
    state.view = state.view === key ? "" : key;
    state.categories = [];
    state.attrs = {};
    state.page = 1;
    refresh();
  }

  /* ---------- Вибрані вузли (кілька) ---------- */
  async function ensureMeta() {
    const missing = state.categories.filter((id) => !state.meta[id]);
    await Promise.all(missing.map(async (id) => {
      try {
        const data = await window.MS.get("/catalog/categories/" + id + "/popup" + viewSuffix());
        state.meta[id] = {
          id: id,
          name: data.category.name,
          path: data.category.path,
          crumbs: data.breadcrumbs || [],
        };
      } catch (err) {
        state.meta[id] = { id: id, name: id, path: id, crumbs: [] };
      }
    }));
  }

  async function loadSelection() {
    if (!state.categories.length) {
      els.breadcrumbs.hidden = true;
      els.breadcrumbs.innerHTML = "";
      return;
    }
    await ensureMeta();
    els.breadcrumbs.innerHTML = "";
    const all = document.createElement("button");
    all.type = "button";
    all.textContent = "Усі товари";
    all.addEventListener("click", clearCategories);
    els.breadcrumbs.appendChild(all);

    state.categories.forEach((id) => {
      const meta = state.meta[id] || { name: id };
      const chip = document.createElement("span");
      chip.className = "sel-chip";
      chip.title = meta.path || meta.name;
      chip.appendChild(document.createTextNode(meta.name));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "sel-remove";
      remove.setAttribute("aria-label", "Прибрати розділ");
      remove.textContent = "×";
      remove.addEventListener("click", () => removeCategory(id));
      chip.appendChild(remove);
      els.breadcrumbs.appendChild(chip);
    });

    // Breadcrumbs останнього вибраного вузла (шлях по дереву)
    const last = state.meta[state.categories[state.categories.length - 1]];
    if (last && last.crumbs && last.crumbs.length) {
      const path = document.createElement("span");
      path.className = "crumbs-path";
      path.textContent = last.crumbs.map((c) => c.name).join(" › ");
      els.breadcrumbs.appendChild(path);
    }
    els.breadcrumbs.hidden = false;
  }

  function addCategory(id, name, path, popupFilters) {
    const key = String(id);
    if (name) state.meta[key] = { id: key, name: name, path: path || name, crumbs: [] };
    if (state.categories.indexOf(key) === -1) {
      state.categories.push(key);
      state.attrs = {}; // набір характеристик змінився — скидаємо фільтри
      state.page = 1;
    }
    Object.keys(popupFilters || {}).forEach((attrId) => {
      if (popupFilters[attrId] && popupFilters[attrId].length) {
        state.attrs[attrId] = popupFilters[attrId];
      }
    });
    refresh();
  }

  function removeCategory(id) {
    const key = String(id);
    state.categories = state.categories.filter((x) => x !== key);
    state.attrs = {};
    state.page = 1;
    refresh();
  }

  function clearCategories() {
    state.categories = [];
    state.attrs = {};
    state.page = 1;
    refresh();
  }

  /* ---------- Плитки розділів ---------- */
  async function loadTiles() {
    const requestUrl = "/catalog/categories" + viewSuffix();
    const cacheKey = window.MSSkeletons.cacheKey(requestUrl);
    const cached = window.MSSkeletons.getCachedData(cacheKey);
    const renderTiles = (data) => {
      const cats = data.categories || [];
      els.tileGrid.replaceChildren();
      if (!cats.length) {
        els.tileGrid.innerHTML = '<p class="text-muted-soft small mb-0">Немає розділів для цього виду товарів.</p>';
        return;
      }
      cats.forEach((cat) => {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "tile" + (state.categories.indexOf(String(cat.id)) !== -1 ? " is-selected" : "");
        tile.innerHTML =
          '<span class="tile-icon" aria-hidden="true">' + categoryIcon(cat) + "</span>" +
          '<span class="tile-name">' + window.MS.escapeHtml(cat.name) + "</span>";
        tile.addEventListener("click", () => window.MSTree.openCategory(cat.id, state.view, state.availability));
        els.tileGrid.appendChild(tile);
      });
    };
    if (cached !== undefined) renderTiles(cached);
    try {
      const data = await window.MS.get(requestUrl);
      window.MSSkeletons.setCachedData(cacheKey, data);
      renderTiles(data);
    } catch (err) {
      const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити розділи.");
      window.MSSkeletons.renderRetryError(
        els.tileGrid,
        message,
        loadTiles,
        cached !== undefined
      );
    }
  }

  /* ---------- Фільтри за характеристиками ---------- */
  async function loadFilters() {
    if (!state.categories.length) {
      els.filtersPanel.hidden = true;
      els.filtersList.innerHTML = "";
      return;
    }
    const p = new URLSearchParams();
    if (state.view) p.set("view", state.view);
    p.set("category", state.categories.join(","));
    p.set("availability", state.availability);
    let data;
    try {
      data = await window.MS.get("/catalog/filters?" + p.toString());
    } catch (err) {
      els.filtersPanel.hidden = true;
      return;
    }
    const attrs = data.attributes || [];
    els.filtersList.innerHTML = "";
    els.filtersPanel.hidden = attrs.length === 0;
    attrs.forEach((attr) => els.filtersList.appendChild(filterRow(attr)));
  }

  function filterRow(attr) {
    const row = document.createElement("div");
    row.className = "filter-row";

    const name = document.createElement("div");
    name.className = "filter-name";
    name.textContent = attr.name + (attr.unit ? ", " + attr.unit : "");
    row.appendChild(name);

    const chips = document.createElement("div");
    chips.className = "filter-chips";
    row.appendChild(chips);

    const selected = state.attrs[String(attr.id)] || [];
    const mode = attr.kind === "multiple" ? "multiple" : "single";
    const addChip = (label, value) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "filter-chip" + (selected.indexOf(value) !== -1 ? " is-on" : "");
      chip.textContent = label;
      chip.addEventListener("click", () => toggleAttr(attr.id, value, mode));
      chips.appendChild(chip);
    };

    if (attr.kind === "number") {
      const current = selected.find((v) => v.indexOf("..") !== -1) || "";
      const parts = current.split("..");
      const range = document.createElement("div");
      range.className = "filter-range";
      range.innerHTML =
        '<input type="number" class="form-control form-control-sm" placeholder="від" value="' +
        window.MS.escapeHtml(parts[0] || "") + '">' +
        "<span>—</span>" +
        '<input type="number" class="form-control form-control-sm" placeholder="до" value="' +
        window.MS.escapeHtml(parts[1] || "") + '">' +
        '<button type="button" class="btn btn-outline-primary btn-sm">ОК</button>';
      const inputs = range.querySelectorAll("input");
      const apply = () => {
        if (inputs[0].value === "" && inputs[1].value === "") setAttr(attr.id, []);
        else setAttr(attr.id, [inputs[0].value + ".." + inputs[1].value]);
      };
      range.querySelector("button").addEventListener("click", apply);
      inputs.forEach((inp) => inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); apply(); }
      }));
      chips.appendChild(range);
    } else {
      let values = (attr.options || []).map((o) => o.value);
      if (attr.kind === "bool") values = ["так", "ні"];
      values.forEach((v) => addChip(v, v));
    }
    addChip("не вказано", "");
    return row;
  }

  function toggleAttr(id, value, mode) {
    const key = String(id);
    const list = state.attrs[key] || [];
    if (value === "") {
      state.attrs[key] = list.length === 1 && list[0] === "" ? [] : [""];
    } else if (mode === "single") {
      state.attrs[key] = list.length === 1 && list[0] === value ? [] : [value];
    } else {
      const cleaned = list.filter((v) => v !== "");
      const at = cleaned.indexOf(value);
      if (at === -1) cleaned.push(value);
      else cleaned.splice(at, 1);
      if (cleaned.length) state.attrs[key] = cleaned;
      else delete state.attrs[key];
    }
    if (state.attrs[key] && !state.attrs[key].length) delete state.attrs[key];
    state.page = 1;
    refresh();
  }

  function setAttr(id, values) {
    const key = String(id);
    if (values.length) state.attrs[key] = values;
    else delete state.attrs[key];
    state.page = 1;
    refresh();
  }

  function productSignature(product) {
    return JSON.stringify([
      product.id,
      product.slug,
      product.name,
      product.product_type,
      product.product_type_display,
      product.category_path,
      product.brand,
      product.price_card,
      product.seller,
      product.in_stock,
      product.image,
      product.is_favorite,
    ]);
  }

  function productsSignature(data, results) {
    return JSON.stringify([
      data.count,
      data.page || 1,
      data.pages || 1,
      results.map(productSignature),
    ]);
  }

  const GPU_ICON =
    '<svg width="1em" height="1em" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<rect x="3" y="5" width="24" height="20" rx="3" fill="#263544" stroke="#101820" stroke-width="2"/>' +
      '<circle cx="13" cy="15" r="6" fill="#c8f04a" stroke="#101820" stroke-width="2"/>' +
      '<circle cx="13" cy="15" r="2" fill="#126c8c"/>' +
      '<path d="M27 10h3v10h-3M8 27v2m5-2v2m5-2v2m4-2v2M3 10H1m2 5H1m2 5H1" ' +
        'stroke="#101820" stroke-width="2" stroke-linecap="round"/>' +
    "</svg>";

  const CATEGORY_ICONS = [
    ["Смартфон", "📱"], ["Планшет", "🔲"], ["Ноутбук", "💻"], ["Комп'ют", "🖥️"],
    ["Відеокарт", GPU_ICON], ["Навушник", "🎧"], ["Колонк", "🔊"], ["Телевіз", "📺"],
    ["Фото", "📷"], ["Дрон", "🚁"], ["Консол", "🕹️"], ["Кросів", "👟"],
    ["Взут", "👞"], ["Одяг", "👕"], ["Сумк", "🎒"], ["Рюкзак", "🎒"],
    ["Мебл", "🛋️"], ["Крісл", "💺"], ["Ліжк", "🛏️"], ["Освіт", "💡"],
    ["Ламп", "💡"], ["Декор", "🖼️"], ["Кухн", "🍳"], ["Інструм", "🧰"],
    ["Сад", "🌱"], ["Косметик", "✨"], ["Макіяж", "💄"], ["Парфум", "🧴"],
    ["Спорт", "🏋️"], ["Гант", "🏋️"], ["Дитяч", "🧸"], ["Книга", "📚"],
    ["Конструктор", "🧱"], ["Кава", "☕"], ["Їжа", "🍽️"], ["Зоотовар", "🐾"],
    ["Гра", "🎮"], ["Steam", "🎮"], ["EA", "🎮"], ["Xbox", "🎮"],
    ["Netflix", "▶️"], ["Game Pass", "🎮"], ["Windows", "💻"], ["Курс", "🎓"],
    ["Шаблон", "🎨"], ["Дизайн", "🎨"], ["Програмув", "💻"], ["Послуг", "🛠️"],
    ["Підписк", "🎫"], ["Подарун", "🎁"], ["Ліценз", "🔑"], ["Музик", "🎵"],
    ["Самокат", "🛴"], ["Очі", "👁️"], ["Худі", "🧥"], ["Браслет", "📿"],
    ["Антивірус", "🛡️"], ["Подарункові набори", "🎟️"], ["Подарункові картки", "🎟️"],
    ["Ubisoft+", "🎫"], ["Ubisoft", "🎮"], ["Батаре", "🔋"], ["Акумулятор", "🔋"],
    ["Битви королів", "🏆"], ["Манікюр", "💅"], ["Пазл", "🧩"],
    ["Комікс", "📖"], ["Квіти", "💐"], ["Шини", "🚗"], ["Ветаптека", "💊"],
    ["Підгуз", "🧷"], ["Коляск", "🛒"], ["Снеки", "🍿"], ["Енергетики", "🥤"],
    ["Органічні продукти", "🌿"], ["Рушник", "🧼"], ["Органайзер", "🗂️"],
    ["Коробк", "📦"], ["Вішал", "🧷"], ["Полиці", "📚"], ["Стелаж", "📚"],
    ["Офісні програми", "🗂️"], ["Офісні пакети", "📄"], ["Хмара", "☁️"],
    ["Домени", "🔒"], ["VPN", "🛡️"], ["Парол", "🔐"], ["Інтеграції", "🔗"],
  ];

  const CATEGORY_ICON_RULES = CATEGORY_ICONS.slice().sort(
    (left, right) => right[0].length - left[0].length
  );
  const CATEGORY_ICON_OVERRIDES = {
    "Електроніка": "🔌",
    "Одяг, взуття, аксесуари": "👕",
    "Дім і меблі": "🛋️",
    "Сад, ремонт, інструменти": "🛠️",
    "Краса й здоров'я": "🩺",
    "Книги, канцелярія, хобі": "📚",
    "Спорт і туризм": "🏕️",
    "Авто й мото": "🚗",
    "Зоотовари": "🐾",
    "Дитячі товари": "🧸",
    "Їжа й напої": "🥗",
    "Подарунки й набори": "🎁",
    "Ігри": "🎮",
    "Ігрові проєкти й ассети": "🧩",
    "Софт і ліцензії": "💾",
    "Навчання й контент": "🎓",
    "Цифрові дизайн-ресурси": "🎨",
    "Подарункові картки й поповнення": "🎟️",
    "Послуги": "🧰",
    "Підписки": "🎫",
    "Самокати": "🛴",
    "Очі": "👁️",
    "Худі": "🧥",
    "Браслети": "📿",
    "Антивіруси": "🛡️",
    "Подарункові набори": "🎟️",
    "Ubisoft+": "🎫",
    "Ubisoft": "🎮",
    "Apple": "🍎",
    "Ванна": "🛁",
    "Офісні програми": "🗂️",
    "Офісні пакети й PDF": "📄",
    "Утиліти й оптимізація": "⚙️",
    "Хмара й хостинг": "☁️",
    "Домени й SSL": "🔒",
    "VPN": "🛡️",
    "Менеджери паролів": "🔐",
    "macOS-утиліти": "🍎",
    "Linux": "🐧",
    "Інтеграції й API": "🔗",
    "Манікюр і педикюр": "💅",
    "Пазли й головоломки": "🧩",
    "Наукова й освітня": "📖",
    "Комікси й манга": "📖",
    "Артбуки й книги про ігри": "🎨",
    "Квіти й букети": "💐",
    "Персоналізовані подарунки": "🎁",
    "Сувеніри": "🎁",
    "Шини й диски": "🚗",
    "Масла й рідини": "🛢️",
    "Птахи й гризуни": "🐦",
    "Ветаптека": "💊",
    "Коляски": "🛒",
    "Підгузки": "🧷",
    "Дитяче харчування": "🍼",
    "Снеки й солодощі": "🍿",
    "Делікатеси": "🍖",
    "Енергетики й напої": "🥤",
    "Органічні продукти": "🌿",
    "Рушники": "🧼",
    "Органайзери": "🗂️",
    "Коробки": "📦",
    "Вішалки": "🧷",
    "Полиці": "📚",
    "Стелажі": "📚",
    "Розробка": "💻",
    "Копірайтинг": "✍️",
    "Переклад": "🌐",
    "Локалізація ігор": "🌐",
    "Субтитри": "💬",
    "Монтаж": "🎬",
    "Озвучка": "🎙️",
    "Саунд-дизайн": "🎛️",
    "Коучинг": "🎓",
    "Тестування (QA)": "🧪",
    "Гітари": "🎸",
    "Клавішні": "🎹",
    "Ударні": "🥁",
    "Вітаміни й добавки": "🍊",
    "Масажери": "💆",
    "Тонометри": "🩺",
    "Термометри": "🌡️",
    "Вітальня": "🛋️",
    "Спальня": "🛏️",
    "Ігрові": "🎮",
    "Офіс": "💻",
    "Кухня й їдальня": "🍽️",
    "Зберігання й організація": "📦",
    "Текстиль": "🧵",
    "Чоловічий одяг": "👔",
    "Жіночий одяг": "👗",
    "Дитячий одяг": "👕",
    "Верхній одяг": "🧥",
    "Взуття": "👞",
    "Прикраси": "💎",
    "Сумки": "👜",
    "Догляд за шкірою": "🧴",
    "Макіяж": "💄",
    "Волосся": "💇",
    "Парфумерія": "🧴",
    "Гігієна": "🦷",
    "Здоров'я": "🩺",
    "Книги": "📚",
    "Художня": "📖",
    "Канцелярія": "✏️",
    "Хобі": "🎨",
    "Настільні ігри": "🎲",
    "Колекційні карти (TCG)": "🃏",
    "Фігурки й колекції": "🧸",
    "Музичні інструменти": "🎸",
    "Творчість": "🎨",
    "Сантехніка": "🚿",
    "Електрика": "💡",
    "Будматеріали": "🧱",
    "Сад і город": "🌱",
    "Інструменти": "🧰",
    "Фітнес": "🏋️",
    "Туризм": "🏕️",
    "Запчастини": "⚙️",
    "Автомобілі": "🚗",
    "Мотоцикли": "🏍️",
    "Для собак": "🐕",
    "Для котів": "🐈",
    "Іграшки": "🧸",
    "Безпека": "🛡️",
    "Операційні системи": "💻",
    "Дизайн і 3D": "🎨",
    "Відео й музика": "🎬",
    "Відео й аудіо": "🎬",
    "Тексти й переклад": "✍️",
    "Технічні послуги": "🛠️",
    "Гейм-послуги": "🎮",
    "Маркетинг": "📈",
    "Консультації": "💬",
    "Навчання наживо": "🎓",
    "Навчальні платформи": "🎓",
    "Подарункові набори за приводом": "🎁",
  };
  const CATEGORY_PATH_ICON_OVERRIDES = {
    "авто й мото › аксесуари": "🧰",
    "авто й мото › аксесуари › килимки": "🧶",
    "авто й мото › аксесуари › тримачі": "📎",
    "авто й мото › електроніка в авто": "📡",
    "авто й мото › електроніка в авто › відеореєстратори": "📹",
    "авто й мото › електроніка в авто › магнітоли": "🎵",
    "авто й мото › електроніка в авто › навігатори": "🧭",
    "авто й мото › запчастини › гальма": "🛑",
    "авто й мото › запчастини › підвіска": "🔩",
    "авто й мото › запчастини › фільтри": "🔧",
    "авто й мото › мототовари": "🏍️",
    "авто й мото › мототовари › екіпірування": "🧥",
    "авто й мото › шини й диски": "🚙",
    "дитячі товари › дитячі меблі": "🪑",
    "дитячі товари › школа": "🎓",
    "дім і меблі › ванна › аксесуари": "🧴",
    "дім і меблі › ванна › килимки": "🧶",
    "дім і меблі › декор › вази": "🏺",
    "дім і меблі › декор › рамки": "📷",
    "дім і меблі › меблі › вітальня › журнальні столики": "☕",
    "дім і меблі › меблі › вітальня › тв-тумби": "📺",
    "дім і меблі › меблі › дитячі меблі": "🪑",
    "дім і меблі › меблі › кухня й їдальня › барні стійки": "🥂",
    "дім і меблі › меблі › кухня й їдальня › столи": "🍽️",
    "дім і меблі › меблі › спальня › ліжка": "🛌",
    "дім і меблі › меблі › ігрові › підставки для vr": "🥽",
    "дім і меблі › меблі › ігрові › стійки під консолі": "🕹️",
    "дім і меблі › меблі › ігрові › ігрові крісла": "🪑",
    "дім і меблі › меблі › ігрові › ігрові столи": "💻",
    "дім і меблі › освітлення › led-стрічки": "✨",
    "дім і меблі › освітлення › бра": "🕯️",
    "дім і меблі › освітлення › люстри": "🏮",
    "дім і меблі › освітлення › нічники": "🌙",
    "дім і меблі › освітлення › торшери": "🔦",
    "дім і меблі › текстиль › ковдри й подушки": "🧸",
    "електроніка › аудіо": "🎧",
    "електроніка › аудіо › колонки › hi-fi": "🎵",
    "електроніка › аудіо › колонки › портативні": "📦",
    "електроніка › аудіо › колонки › саундбари": "🎬",
    "електроніка › аудіо › колонки › смарт-колонки": "🤖",
    "електроніка › аудіо › мікрофони › usb": "🔌",
    "електроніка › аудіо › мікрофони › для стрімів": "🎥",
    "електроніка › аудіо › мікрофони › петлички": "🎤",
    "електроніка › аудіо › навушники › бездротові tws": "📡",
    "електроніка › аудіо › навушники › накладні": "🎧",
    "електроніка › аудіо › навушники › повнорозмірні": "🎼",
    "електроніка › аудіо › навушники › студійні": "🎙️",
    "електроніка › аудіо › навушники › ігрові": "🎮",
    "електроніка › живлення й енергія": "⚡",
    "електроніка › живлення й енергія › генератори": "🚜",
    "електроніка › живлення й енергія › електростанції": "🏕️",
    "електроніка › комп'ютери й ноутбуки › комплектуючі": "🧩",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › відеокарти › amd": "🔺",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › відеокарти › intel": "🔵",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › відеокарти › nvidia": "🟩",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › відеокарти › професійні": "💼",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › охолодження › повітряне": "🌬️",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › охолодження › рідинне": "💧",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › процесори › amd": "🔺",
    "електроніка › комп'ютери й ноутбуки › комплектуючі › процесори › intel": "🔵",
    "електроніка › комп'ютери й ноутбуки › мережеве обладнання": "📡",
    "електроніка › комп'ютери й ноутбуки › мережеве обладнання › wi-fi mesh": "📶",
    "електроніка › комп'ютери й ноутбуки › монітори": "📺",
    "електроніка › комп'ютери й ноутбуки › монітори › 4k/8k": "🔍",
    "електроніка › комп'ютери й ноутбуки › монітори › вигнуті": "🌀",
    "електроніка › комп'ютери й ноутбуки › монітори › офісні": "💼",
    "електроніка › комп'ютери й ноутбуки › монітори › портативні": "📦",
    "електроніка › комп'ютери й ноутбуки › настільні пк › міні-пк": "📦",
    "електроніка › комп'ютери й ноутбуки › настільні пк › офісні": "💼",
    "електроніка › комп'ютери й ноутбуки › настільні пк › сервери": "🗄️",
    "електроніка › комп'ютери й ноутбуки › ноутбуки › chromebook": "🌐",
    "електроніка › комп'ютери й ноутбуки › ноутбуки › для дизайну": "🎨",
    "електроніка › комп'ютери й ноутбуки › ноутбуки › офісні": "💼",
    "електроніка › комп'ютери й ноутбуки › ноутбуки › трансформери": "⚡",
    "електроніка › комп'ютери й ноутбуки › периферія": "⌨️",
    "електроніка › комп'ютери й ноутбуки › периферія › док-станції": "🔌",
    "електроніка › комп'ютери й ноутбуки › периферія › хаби": "🗂️",
    "електроніка › комп'ютери й ноутбуки › принтери й сканери › 3d-принтери": "🧱",
    "електроніка › комп'ютери й ноутбуки › принтери й сканери › лазерні": "🔦",
    "електроніка › комп'ютери й ноутбуки › принтери й сканери › мфу": "📠",
    "електроніка › планшети й e-readers › аксесуари": "🧳",
    "електроніка › побутова техніка": "🧹",
    "електроніка › побутова техніка › кліматична техніка": "🌡️",
    "електроніка › побутова техніка › кухонна техніка": "🍳",
    "електроніка › побутова техніка › кухонна техніка › кавомашини › капсульні": "💊",
    "електроніка › побутова техніка › кухонна техніка › кавомашини › ріжкові": "🥛",
    "електроніка › побутова техніка › кухонна техніка › мультипечі": "🍲",
    "електроніка › побутова техніка › кухонна техніка › міксери": "🥄",
    "електроніка › побутова техніка › кухонна техніка › холодильники › side-by-side": "🚪",
    "електроніка › побутова техніка › кухонна техніка › холодильники › мінібари": "🧊",
    "електроніка › побутова техніка › кухонна техніка › чайники": "🍵",
    "електроніка › побутова техніка › особистий догляд": "🪒",
    "електроніка › побутова техніка › прання й догляд": "🧺",
    "електроніка › побутова техніка › прання й догляд › відпарювачі": "💨",
    "електроніка › побутова техніка › прибирання": "🧹",
    "електроніка › побутова техніка › прибирання › вертикальні пилососи › бездротові": "🔋",
    "електроніка › побутова техніка › прибирання › вертикальні пилососи › дротові": "🔌",
    "електроніка › побутова техніка › прибирання › пилососи для дому": "🏠",
    "електроніка › побутова техніка › прибирання › пилососи для дому › контейнерні": "📦",
    "електроніка › побутова техніка › прибирання › пилососи для дому › мийні": "💧",
    "електроніка › побутова техніка › прибирання › пилососи для дому › мішкові": "🧹",
    "електроніка › побутова техніка › прибирання › роботи-пилососи › гібридні": "🔄",
    "електроніка › побутова техніка › прибирання › роботи-пилососи › зі станцією самоочищення": "🧼",
    "електроніка › побутова техніка › прибирання › роботи-пилососи › мийні": "💧",
    "електроніка › побутова техніка › прибирання › роботи-пилососи › сухі": "🌪️",
    "електроніка › розумний дім": "🏠",
    "електроніка › розумний дім › хаби": "🗂️",
    "електроніка › смартфони й зв'язок › фітнес-браслети": "🏋️",
    "електроніка › тв і домашній кінотеатр": "📺",
    "електроніка › тв і домашній кінотеатр › тв-приставки й медіаплеєри": "📻",
    "електроніка › тв і домашній кінотеатр › телевізори › led": "💡",
    "електроніка › тв і домашній кінотеатр › телевізори › oled": "🌈",
    "електроніка › тв і домашній кінотеатр › телевізори › qled": "🔆",
    "електроніка › фото й відео › дрони › fpv": "🥽",
    "електроніка › фото й відео › дрони › любительські": "📷",
    "електроніка › фото й відео › дрони › професійні": "💼",
    "електроніка › фото й відео › камери спостереження": "🔒",
    "електроніка › фото й відео › фотоапарати › бездзеркальні": "🖼️",
    "електроніка › фото й відео › фотоапарати › дзеркальні": "📸",
    "електроніка › фото й відео › фотоапарати › компакти": "🔍",
    "електроніка › фото й відео › фотоапарати › інстакс": "🎞️",
    "електроніка › ігрові пристрої": "🎮",
    "електроніка › ігрові пристрої › vr/ar › аксесуари": "🧳",
    "електроніка › ігрові пристрої › vr/ar › контролери": "🎮",
    "електроніка › ігрові пристрої › аксесуари для консолей": "🎒",
    "електроніка › ігрові пристрої › аксесуари для консолей › зарядні станції": "🔋",
    "електроніка › ігрові пристрої › аксесуари для консолей › кейси": "🧳",
    "електроніка › ігрові пристрої › аксесуари для консолей › накопичувачі": "💾",
    "електроніка › ігрові пристрої › консолі › playstation": "🕹️",
    "електроніка › ігрові пристрої › консолі › xbox": "🏆",
    "електроніка › ігрові пристрої › консолі › портативні пк": "📱",
    "електроніка › ігрові пристрої › консолі › ретро": "🕰️",
    "електроніка › ігрові пристрої › стрім-обладнання": "🎥",
    "електроніка › ігрові пристрої › стрім-обладнання › світло": "💡",
    "електроніка › ігрові пристрої › ігрова периферія": "⌨️",
    "електроніка › ігрові пристрої › ігрова периферія › килимки": "🧶",
    "електроніка › ігрові пристрої › ігрові монітори › 144 гц": "⏱️",
    "електроніка › ігрові пристрої › ігрові монітори › 240 гц+": "⚡",
    "електроніка › ігрові пристрої › ігрові монітори › oled": "🌈",
    "електроніка › ігрові пристрої › ігрові монітори › вигнуті": "🌀",
    "електроніка › ігрові пристрої › ігрові пк й ноутбуки": "💻",
    "електроніка › ігрові пристрої › ігрові пк й ноутбуки › ігрові ноутбуки": "💻",
    "електроніка › ігрові пристрої › ігрові пк й ноутбуки › ігрові пк": "🖥️",
    "зоотовари › акваріумістика › акваріуми": "🐠",
    "зоотовари › акваріумістика › корми": "🐟",
    "зоотовари › акваріумістика › фільтри": "🔧",
    "зоотовари › для котів › гігієна": "🧼",
    "зоотовари › для котів › когтеточки": "🐾",
    "зоотовари › для котів › корми": "🐟",
    "зоотовари › для котів › ласощі": "🦴",
    "зоотовари › для котів › лежанки": "🛏️",
    "зоотовари › для котів › миски й поїлки": "🥣",
    "зоотовари › для котів › наповнювачі": "🧹",
    "зоотовари › для котів › одяг і комбінезони": "👕",
    "зоотовари › для котів › переноски": "🧳",
    "зоотовари › для котів › іграшки": "🧶",
    "зоотовари › для собак › корми": "🦴",
    "зоотовари › для собак › лежаки": "🛏️",
    "зоотовари › для собак › одяг": "👕",
    "зоотовари › для собак › іграшки": "🎾",
    "книги, канцелярія, хобі › канцелярія › блокноти": "📓",
    "книги, канцелярія, хобі › канцелярія › маркери": "🖍️",
    "книги, канцелярія, хобі › канцелярія › планери": "📅",
    "книги, канцелярія, хобі › канцелярія › ручки": "🖊️",
    "книги, канцелярія, хобі › канцелярія › скетчбуки": "🎨",
    "книги, канцелярія, хобі › книги › наукова й освітня": "🔬",
    "книги, канцелярія, хобі › книги › художня": "📚",
    "книги, канцелярія, хобі › книги › художня › детективи": "🕵️",
    "книги, канцелярія, хобі › книги › художня › романи": "💌",
    "книги, канцелярія, хобі › книги › художня › фантастика": "🚀",
    "книги, канцелярія, хобі › колекційні карти (tcg) › magic": "🔮",
    "книги, канцелярія, хобі › колекційні карти (tcg) › pokemon": "🐾",
    "книги, канцелярія, хобі › колекційні карти (tcg) › yu-gi-oh": "🐉",
    "книги, канцелярія, хобі › конструктори › збірні моделі": "🚂",
    "книги, канцелярія, хобі › конструктори › радіокеровані": "🚗",
    "книги, канцелярія, хобі › музичні інструменти › аксесуари": "🎼",
    "книги, канцелярія, хобі › настільні ігри › party": "🎉",
    "книги, канцелярія, хобі › настільні ігри › карткові": "🃏",
    "книги, канцелярія, хобі › настільні ігри › кооперативні": "🤝",
    "книги, канцелярія, хобі › настільні ігри › рольові": "🧙",
    "книги, канцелярія, хобі › настільні ігри › стратегії": "♟️",
    "книги, канцелярія, хобі › творчість › 3d-ручки": "✒️",
    "книги, канцелярія, хобі › творчість › вишивка": "🧵",
    "книги, канцелярія, хобі › творчість › набори для творчості": "🎁",
    "книги, канцелярія, хобі › фігурки й колекції › мініатюри": "🧍",
    "книги, канцелярія, хобі › фігурки й колекції › статуетки": "🗿",
    "краса й здоров'я › волосся › маски": "🧼",
    "краса й здоров'я › волосся › стайлінг": "💇",
    "краса й здоров'я › волосся › фарби": "🎨",
    "краса й здоров'я › волосся › шампуні": "🧴",
    "краса й здоров'я › гігієна › гелі": "🧴",
    "краса й здоров'я › гігієна › дезодоранти": "💨",
    "краса й здоров'я › догляд за шкірою › обличчя": "👩",
    "краса й здоров'я › догляд за шкірою › сонцезахисні": "🕶️",
    "краса й здоров'я › догляд за шкірою › тіло": "🧴",
    "краса й здоров'я › догляд за шкірою › чоловічі": "🧔",
    "краса й здоров'я › макіяж › губи": "💋",
    "краса й здоров'я › макіяж › обличчя": "💄",
    "краса й здоров'я › макіяж › очі": "👁️",
    "краса й здоров'я › макіяж › пензлі": "🖌️",
    "краса й здоров'я › парфумерія": "💐",
    "краса й здоров'я › парфумерія › жіноча": "👩",
    "краса й здоров'я › парфумерія › мініатюри": "🧍",
    "краса й здоров'я › парфумерія › унісекс": "🌐",
    "краса й здоров'я › парфумерія › чоловіча": "👔",
    "навчання й контент › e-books й аудіокниги": "📚",
    "навчання й контент › відеоуроки й майстер-класи": "🎥",
    "навчання й контент › гайди й чекліст-пакети": "📖",
    "навчання й контент › курси › бізнес і маркетинг": "📈",
    "навчання й контент › курси › геймдев": "🎮",
    "навчання й контент › курси › геймдев › godot": "🐸",
    "навчання й контент › курси › геймдев › unreal": "🌌",
    "навчання й контент › курси › геймдев › ігровий дизайн": "🎨",
    "навчання й контент › курси › дизайн › 2d-ілюстрація": "🖌️",
    "навчання й контент › курси › дизайн › 3d": "🧊",
    "навчання й контент › курси › дизайн › ui/ux": "🖥️",
    "навчання й контент › курси › мови": "🌐",
    "навчання й контент › курси › мови › англійська": "🇬🇧",
    "навчання й контент › курси › мови › німецька": "🇩🇪",
    "навчання й контент › курси › мови › польська": "🇵🇱",
    "навчання й контент › курси › програмування › c#": "🔣",
    "навчання й контент › курси › програмування › c++": "➕",
    "навчання й контент › курси › програмування › django": "🏗️",
    "навчання й контент › курси › програмування › javascript": "📜",
    "навчання й контент › курси › програмування › python": "🐍",
    "навчання й контент › сертифікації": "📜",
    "одяг, взуття, аксесуари › аксесуари › аксесуари для пристроїв › зарядки й кабелі": "🔌",
    "одяг, взуття, аксесуари › аксесуари › аксесуари для пристроїв › захисне скло": "🔲",
    "одяг, взуття, аксесуари › аксесуари › аксесуари для пристроїв › селфі-палиці": "🤳",
    "одяг, взуття, аксесуари › аксесуари › аксесуари для пристроїв › тримачі": "📎",
    "одяг, взуття, аксесуари › аксесуари › аксесуари для пристроїв › чохли й накладки": "🧳",
    "одяг, взуття, аксесуари › аксесуари › гаманці › картхолдери": "💳",
    "одяг, взуття, аксесуари › аксесуари › гаманці › монетниці": "💰",
    "одяг, взуття, аксесуари › аксесуари › гаманці › портмоне": "💵",
    "одяг, взуття, аксесуари › аксесуари › годинники › кишенькові": "🕰️",
    "одяг, взуття, аксесуари › аксесуари › годинники › наручні": "⏱️",
    "одяг, взуття, аксесуари › аксесуари › годинники › ремінці й браслети": "📿",
    "одяг, взуття, аксесуари › аксесуари › головні убори › берети": "🎩",
    "одяг, взуття, аксесуари › аксесуари › головні убори › панами": "👒",
    "одяг, взуття, аксесуари › аксесуари › головні убори › шапки": "🧶",
    "одяг, взуття, аксесуари › аксесуари › окуляри › оптичні": "👓",
    "одяг, взуття, аксесуари › аксесуари › окуляри › футляри": "🧳",
    "одяг, взуття, аксесуари › аксесуари › прикраси › брошки": "📌",
    "одяг, взуття, аксесуари › аксесуари › прикраси › каблучки": "💍",
    "одяг, взуття, аксесуари › аксесуари › прикраси › ланцюжки": "⛓️",
    "одяг, взуття, аксесуари › аксесуари › прикраси › сережки": "👂",
    "одяг, взуття, аксесуари › аксесуари › ремені › класичні": "👛",
    "одяг, взуття, аксесуари › аксесуари › ремені › повсякденні": "👖",
    "одяг, взуття, аксесуари › аксесуари › ремені › підтяжки": "🎗️",
    "одяг, взуття, аксесуари › аксесуари › шарфи й рукавиці › рукавиці": "🧤",
    "одяг, взуття, аксесуари › аксесуари › шарфи й рукавиці › рукавички": "🧶",
    "одяг, взуття, аксесуари › аксесуари › шарфи й рукавиці › шарфи": "🧵",
    "одяг, взуття, аксесуари › взуття › кросівки › баскетбольні": "🏀",
    "одяг, взуття, аксесуари › взуття › кросівки › бігові": "🏃",
    "одяг, взуття, аксесуари › геймерський і фандом-одяг": "👾",
    "одяг, взуття, аксесуари › геймерський і фандом-одяг › кепки": "🧢",
    "одяг, взуття, аксесуари › геймерський і фандом-одяг › шкарпетки з принтами": "🧦",
    "одяг, взуття, аксесуари › дитячий одяг": "🧸",
    "одяг, взуття, аксесуари › дитячий одяг › для дівчаток": "👗",
    "одяг, взуття, аксесуари › дитячий одяг › для немовлят": "🍼",
    "одяг, взуття, аксесуари › дитячий одяг › шкільна форма": "🎓",
    "одяг, взуття, аксесуари › жіночий одяг › блузки й топи": "👚",
    "одяг, взуття, аксесуари › жіночий одяг › білизна й домашній одяг": "👙",
    "одяг, взуття, аксесуари › жіночий одяг › купальники": "🏊",
    "одяг, взуття, аксесуари › жіночий одяг › светри й кардигани": "🧶",
    "одяг, взуття, аксесуари › жіночий одяг › спідниці": "🩳",
    "одяг, взуття, аксесуари › жіночий одяг › штани й джинси": "👖",
    "одяг, взуття, аксесуари › сумки й рюкзаки › рюкзаки › для ноутбука": "💻",
    "одяг, взуття, аксесуари › сумки й рюкзаки › рюкзаки › міські": "🏙️",
    "одяг, взуття, аксесуари › сумки й рюкзаки › рюкзаки › туристичні": "🏕️",
    "одяг, взуття, аксесуари › сумки й рюкзаки › сумки › поясні": "💼",
    "одяг, взуття, аксесуари › сумки й рюкзаки › сумки › через плече": "👝",
    "одяг, взуття, аксесуари › сумки й рюкзаки › сумки › шопери": "🧺",
    "одяг, взуття, аксесуари › чоловічий одяг › верхній одяг › дощовики": "☔",
    "одяг, взуття, аксесуари › чоловічий одяг › верхній одяг › жилети": "🦺",
    "одяг, взуття, аксесуари › чоловічий одяг › верхній одяг › пальта": "🧣",
    "одяг, взуття, аксесуари › чоловічий одяг › костюми й піджаки": "🤵",
    "одяг, взуття, аксесуари › чоловічий одяг › нижня білизна й шкарпетки": "🧦",
    "одяг, взуття, аксесуари › чоловічий одяг › светри": "🧶",
    "одяг, взуття, аксесуари › чоловічий одяг › толстовки": "🎽",
    "одяг, взуття, аксесуари › чоловічий одяг › футболки й поло": "👕",
    "одяг, взуття, аксесуари › чоловічий одяг › худі": "👚",
    "одяг, взуття, аксесуари › чоловічий одяг › шорти": "🩳",
    "одяг, взуття, аксесуари › чоловічий одяг › штани й джинси": "👖",
    "подарунки й набори › персоналізовані подарунки": "📦",
    "подарунки й набори › подарункові набори за приводом › весілля": "💍",
    "подарунки й набори › подарункові набори за приводом › день народження": "🎂",
    "подарунки й набори › подарункові набори за приводом › новий рік": "🎄",
    "подарунки й набори › сувеніри": "🗿",
    "подарункові картки й поповнення › google play": "📱",
    "подарункові картки й поповнення › nintendo eshop": "🕹️",
    "подарункові картки й поповнення › playstation store": "🎮",
    "подарункові картки й поповнення › spotify": "🎵",
    "подарункові картки й поповнення › steam": "💾",
    "подарункові картки й поповнення › xbox": "🏆",
    "подарункові картки й поповнення › мобільне поповнення": "📲",
    "послуги › гейм-послуги › моддинг на замовлення": "🎨",
    "послуги › гейм-послуги › налаштування discord-спільнот": "💬",
    "послуги › гейм-послуги › налаштування ігрових серверів": "🖥️",
    "послуги › дизайн › 3d-моделювання на замовлення": "🧊",
    "послуги › дизайн › ui/ux": "🖥️",
    "послуги › дизайн › анімація": "🎞️",
    "послуги › дизайн › логотипи й айдентика": "💎",
    "послуги › дизайн › логотипи й брендинг": "🔷",
    "послуги › дизайн › постери й банери": "🖼️",
    "послуги › розробка": "🧰",
    "послуги › розробка › автоматизація й скрипти": "⚙️",
    "послуги › розробка › боти": "🤖",
    "послуги › розробка › боти › discord": "💬",
    "послуги › розробка › боти › telegram": "✈️",
    "послуги › розробка › веб": "🌐",
    "послуги › розробка › веб › django": "🏗️",
    "послуги › розробка › веб › fastapi": "⚡",
    "послуги › розробка › веб › flask": "🧪",
    "послуги › розробка › веб › лендінги": "📄",
    "послуги › розробка › веб › інтернет-магазини": "🛒",
    "послуги › розробка › мобільні додатки": "📱",
    "послуги › розробка › ігри › godot": "🐸",
    "послуги › розробка › ігри › unreal": "🌌",
    "послуги › тексти й переклад › переклад": "📜",
    "послуги › технічні послуги › встановлення пз": "📦",
    "послуги › технічні послуги › відновлення даних": "💾",
    "послуги › технічні послуги › збірка пк": "🔧",
    "послуги › технічні послуги › налаштування мережі": "📡",
    "послуги › технічні послуги › налаштування пк": "⚙️",
    "послуги › інше": "💬",
    "послуги › інше › консультації › it": "💻",
    "послуги › інше › консультації › бізнес": "💼",
    "послуги › інше › консультації › кар'єра": "🎓",
    "послуги › інше › маркетинг › seo": "🔍",
    "послуги › інше › маркетинг › smm": "📱",
    "послуги › інше › маркетинг › реклама": "📣",
    "послуги › інше › навчання наживо › воркшопи": "🧰",
    "послуги › інше › навчання наживо › репетитор": "📚",
    "підписки › навчання й продуктивність": "📚",
    "підписки › софт": "💾",
    "підписки › софт › adobe creative cloud": "🎨",
    "підписки › софт › ai-сервіси": "💡",
    "підписки › софт › jetbrains": "💻",
    "підписки › софт › microsoft 365": "📄",
    "підписки › стрімінг і медіа": "🎬",
    "підписки › стрімінг і медіа › apple music": "🍎",
    "підписки › стрімінг і медіа › disney+": "🎞️",
    "підписки › стрімінг і медіа › spotify": "🎵",
    "підписки › стрімінг і медіа › youtube premium": "📺",
    "підписки › хмара й хостинг › vps": "🖥️",
    "підписки › хмара й хостинг › хостинг": "🌐",
    "підписки › ігрові › ea play": "🏟️",
    "підписки › ігрові › nintendo online": "🕹️",
    "підписки › ігрові › ps plus": "🏆",
    "сад, ремонт, інструменти › басейни й відпочинок › мангали": "🔥",
    "сад, ремонт, інструменти › басейни й відпочинок › намети": "⛺",
    "сад, ремонт, інструменти › будматеріали › підлога": "🏠",
    "сад, ремонт, інструменти › будматеріали › фарби": "🎨",
    "сад, ремонт, інструменти › будматеріали › шпаклівки": "🧰",
    "сад, ремонт, інструменти › електрика › вимикачі": "🔘",
    "сад, ремонт, інструменти › електрика › розетки": "🔌",
    "сад, ремонт, інструменти › сад і город › газонокосарки": "🚜",
    "сад, ремонт, інструменти › сад і город › теплиці": "🏠",
    "сад, ремонт, інструменти › сантехніка › змішувачі": "🚰",
    "сад, ремонт, інструменти › сантехніка › труби": "🔩",
    "сад, ремонт, інструменти › сантехніка › унітази": "🚽",
    "сад, ремонт, інструменти › інструменти › електроінструмент › багатофункціональні інструменти": "🧰",
    "сад, ремонт, інструменти › інструменти › електроінструмент › болгарки": "⚙️",
    "сад, ремонт, інструменти › інструменти › електроінструмент › гайковерти": "🔧",
    "сад, ремонт, інструменти › інструменти › електроінструмент › дрилі": "🔧",
    "сад, ремонт, інструменти › інструменти › електроінструмент › електропили": "🔨",
    "сад, ремонт, інструменти › інструменти › електроінструмент › електрорубанки": "🧰",
    "сад, ремонт, інструменти › інструменти › електроінструмент › лобзики": "🛠️",
    "сад, ремонт, інструменти › інструменти › електроінструмент › перфоратори": "🔨",
    "сад, ремонт, інструменти › інструменти › електроінструмент › фрезери": "⚙️",
    "сад, ремонт, інструменти › інструменти › електроінструмент › циркулярні пили": "⚙️",
    "сад, ремонт, інструменти › інструменти › електроінструмент › шуруповерти": "🔩",
    "софт і ліцензії › безпека › vpn": "🔒",
    "софт і ліцензії › відео й музика › daw": "🎛️",
    "софт і ліцензії › відео й музика › плагіни": "🔌",
    "софт і ліцензії › дизайн і 3d › blender-аддони": "🧊",
    "софт і ліцензії › дизайн і 3d › cad": "📐",
    "софт і ліцензії › дизайн і 3d › figma-плагіни": "🧩",
    "софт і ліцензії › розробка": "🧰",
    "спорт і туризм › велоспорт": "🚲",
    "спорт і туризм › велоспорт › велосипеди": "🚲",
    "спорт і туризм › велоспорт › шоломи": "🧢",
    "спорт і туризм › водний спорт": "🌊",
    "спорт і туризм › зимовий спорт": "❄️",
    "спорт і туризм › командні види": "⚽",
    "спорт і туризм › командні види › баскетбол": "🏀",
    "спорт і туризм › командні види › волейбол": "🏐",
    "спорт і туризм › командні види › футбол": "⚽",
    "спорт і туризм › туризм › ліхтарі": "🔦",
    "спорт і туризм › туризм › намети": "⛺",
    "спорт і туризм › туризм › спальники": "🛏️",
    "спорт і туризм › фітнес › гантелі": "💪",
    "спорт і туризм › фітнес › еспандери": "🔗",
    "спорт і туризм › фітнес › килимки": "🧘",
    "цифрові дизайн-ресурси › мокапи": "🖼️",
    "цифрові дизайн-ресурси › презентації": "📊",
    "цифрові дизайн-ресурси › пресети › lightroom": "📷",
    "цифрові дизайн-ресурси › пресети › lut": "🌈",
    "цифрові дизайн-ресурси › стокова музика": "🎵",
    "цифрові дизайн-ресурси › стокові відео": "🎥",
    "цифрові дизайн-ресурси › стокові фото": "📷",
    "цифрові дизайн-ресурси › шаблони відео": "🎬",
    "цифрові дизайн-ресурси › шаблони сайтів": "🌐",
    "цифрові дизайн-ресурси › шрифти": "🔤",
    "цифрові дизайн-ресурси › іконки": "🔣",
    "ігри › pc": "💻",
    "ігри › pc › battle.net": "❄️",
    "ігри › pc › battle.net › rpg": "🧙",
    "ігри › pc › battle.net › rpg › action-rpg": "🗡️",
    "ігри › pc › battle.net › rpg › crpg": "🛡️",
    "ігри › pc › battle.net › rpg › jrpg": "🧝",
    "ігри › pc › battle.net › rpg › mmorpg": "👑",
    "ігри › pc › battle.net › rpg › roguelike": "🎲",
    "ігри › pc › battle.net › виживання й крафт": "🏕️",
    "ігри › pc › battle.net › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › battle.net › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › battle.net › головоломки й казуальні": "🧩",
    "ігри › pc › battle.net › гонки й спорт": "🏁",
    "ігри › pc › battle.net › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › battle.net › гонки й спорт › симулятори": "🚜",
    "ігри › pc › battle.net › гонки й спорт › файтинги": "🥊",
    "ігри › pc › battle.net › гонки й спорт › футбол": "⚽",
    "ігри › pc › battle.net › для дітей і родини": "🧸",
    "ігри › pc › battle.net › екшен": "⚔️",
    "ігри › pc › battle.net › екшен › hack and slash": "⚔️",
    "ігри › pc › battle.net › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › battle.net › екшен › шутери від третього": "🔫",
    "ігри › pc › battle.net › пригоди": "🗺️",
    "ігри › pc › battle.net › пригоди › walking-sim": "🚶",
    "ігри › pc › battle.net › пригоди › відкритий світ": "🌍",
    "ігри › pc › battle.net › пригоди › візуальні романи": "💌",
    "ігри › pc › battle.net › пригоди › квести": "🗝️",
    "ігри › pc › battle.net › симулятори": "🚜",
    "ігри › pc › battle.net › симулятори › будівельні": "🏗️",
    "ігри › pc › battle.net › симулятори › життя": "🏠",
    "ігри › pc › battle.net › симулятори › спорт": "🏅",
    "ігри › pc › battle.net › симулятори › транспорт": "🚘",
    "ігри › pc › battle.net › симулятори › фермерські": "🚜",
    "ігри › pc › battle.net › стратегії": "♟️",
    "ігри › pc › battle.net › стратегії › 4x": "🌌",
    "ігри › pc › battle.net › стратегії › rts": "🗺️",
    "ігри › pc › battle.net › стратегії › tower defense": "🏰",
    "ігри › pc › battle.net › стратегії › карткові": "🃏",
    "ігри › pc › battle.net › стратегії › покрокові": "🥇",
    "ігри › pc › battle.net › хорор": "👻",
    "ігри › pc › battle.net › хорор › виживання": "⛺",
    "ігри › pc › battle.net › хорор › кооперативний": "🤝",
    "ігри › pc › battle.net › хорор › психологічний": "🧠",
    "ігри › pc › battle.net › інді": "🎨",
    "ігри › pc › ea app": "🏟️",
    "ігри › pc › ea app › rpg": "🧙",
    "ігри › pc › ea app › rpg › action-rpg": "🗡️",
    "ігри › pc › ea app › rpg › crpg": "🛡️",
    "ігри › pc › ea app › rpg › jrpg": "🧝",
    "ігри › pc › ea app › rpg › mmorpg": "👑",
    "ігри › pc › ea app › rpg › roguelike": "🎲",
    "ігри › pc › ea app › виживання й крафт": "🏕️",
    "ігри › pc › ea app › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › ea app › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › ea app › головоломки й казуальні": "🧩",
    "ігри › pc › ea app › гонки й спорт": "🏁",
    "ігри › pc › ea app › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › ea app › гонки й спорт › симулятори": "🚜",
    "ігри › pc › ea app › гонки й спорт › файтинги": "🥊",
    "ігри › pc › ea app › гонки й спорт › футбол": "⚽",
    "ігри › pc › ea app › для дітей і родини": "🧸",
    "ігри › pc › ea app › екшен": "⚔️",
    "ігри › pc › ea app › екшен › hack and slash": "⚔️",
    "ігри › pc › ea app › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › ea app › екшен › шутери від третього": "🔫",
    "ігри › pc › ea app › пригоди": "🗺️",
    "ігри › pc › ea app › пригоди › walking-sim": "🚶",
    "ігри › pc › ea app › пригоди › відкритий світ": "🌍",
    "ігри › pc › ea app › пригоди › візуальні романи": "💌",
    "ігри › pc › ea app › пригоди › квести": "🗝️",
    "ігри › pc › ea app › симулятори": "🚜",
    "ігри › pc › ea app › симулятори › будівельні": "🏗️",
    "ігри › pc › ea app › симулятори › життя": "🏠",
    "ігри › pc › ea app › симулятори › спорт": "🏅",
    "ігри › pc › ea app › симулятори › транспорт": "🚘",
    "ігри › pc › ea app › симулятори › фермерські": "🚜",
    "ігри › pc › ea app › стратегії": "♟️",
    "ігри › pc › ea app › стратегії › 4x": "🌌",
    "ігри › pc › ea app › стратегії › rts": "🗺️",
    "ігри › pc › ea app › стратегії › tower defense": "🏰",
    "ігри › pc › ea app › стратегії › карткові": "🃏",
    "ігри › pc › ea app › стратегії › покрокові": "🥇",
    "ігри › pc › ea app › хорор": "👻",
    "ігри › pc › ea app › хорор › виживання": "⛺",
    "ігри › pc › ea app › хорор › кооперативний": "🤝",
    "ігри › pc › ea app › хорор › психологічний": "🧠",
    "ігри › pc › ea app › інді": "🎨",
    "ігри › pc › epic games": "🏰",
    "ігри › pc › epic games › rpg": "🧙",
    "ігри › pc › epic games › rpg › action-rpg": "🗡️",
    "ігри › pc › epic games › rpg › crpg": "🛡️",
    "ігри › pc › epic games › rpg › jrpg": "🧝",
    "ігри › pc › epic games › rpg › mmorpg": "👑",
    "ігри › pc › epic games › rpg › roguelike": "🎲",
    "ігри › pc › epic games › виживання й крафт": "🏕️",
    "ігри › pc › epic games › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › epic games › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › epic games › головоломки й казуальні": "🧩",
    "ігри › pc › epic games › гонки й спорт": "🏁",
    "ігри › pc › epic games › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › epic games › гонки й спорт › симулятори": "🚜",
    "ігри › pc › epic games › гонки й спорт › файтинги": "🥊",
    "ігри › pc › epic games › гонки й спорт › футбол": "⚽",
    "ігри › pc › epic games › для дітей і родини": "🧸",
    "ігри › pc › epic games › екшен": "⚔️",
    "ігри › pc › epic games › екшен › hack and slash": "⚔️",
    "ігри › pc › epic games › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › epic games › екшен › шутери від третього": "🔫",
    "ігри › pc › epic games › пригоди": "🗺️",
    "ігри › pc › epic games › пригоди › walking-sim": "🚶",
    "ігри › pc › epic games › пригоди › відкритий світ": "🌍",
    "ігри › pc › epic games › пригоди › візуальні романи": "💌",
    "ігри › pc › epic games › пригоди › квести": "🗝️",
    "ігри › pc › epic games › симулятори": "🚜",
    "ігри › pc › epic games › симулятори › будівельні": "🏗️",
    "ігри › pc › epic games › симулятори › життя": "🏠",
    "ігри › pc › epic games › симулятори › спорт": "🏅",
    "ігри › pc › epic games › симулятори › транспорт": "🚘",
    "ігри › pc › epic games › симулятори › фермерські": "🚜",
    "ігри › pc › epic games › стратегії": "♟️",
    "ігри › pc › epic games › стратегії › 4x": "🌌",
    "ігри › pc › epic games › стратегії › rts": "🗺️",
    "ігри › pc › epic games › стратегії › tower defense": "🏰",
    "ігри › pc › epic games › стратегії › карткові": "🃏",
    "ігри › pc › epic games › стратегії › покрокові": "🥇",
    "ігри › pc › epic games › хорор": "👻",
    "ігри › pc › epic games › хорор › виживання": "⛺",
    "ігри › pc › epic games › хорор › кооперативний": "🤝",
    "ігри › pc › epic games › хорор › психологічний": "🧠",
    "ігри › pc › epic games › інді": "🎨",
    "ігри › pc › gog": "📀",
    "ігри › pc › gog › rpg": "🧙",
    "ігри › pc › gog › rpg › action-rpg": "🗡️",
    "ігри › pc › gog › rpg › crpg": "🛡️",
    "ігри › pc › gog › rpg › jrpg": "🧝",
    "ігри › pc › gog › rpg › mmorpg": "👑",
    "ігри › pc › gog › rpg › roguelike": "🎲",
    "ігри › pc › gog › виживання й крафт": "🏕️",
    "ігри › pc › gog › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › gog › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › gog › головоломки й казуальні": "🧩",
    "ігри › pc › gog › гонки й спорт": "🏁",
    "ігри › pc › gog › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › gog › гонки й спорт › симулятори": "🚜",
    "ігри › pc › gog › гонки й спорт › файтинги": "🥊",
    "ігри › pc › gog › гонки й спорт › футбол": "⚽",
    "ігри › pc › gog › для дітей і родини": "🧸",
    "ігри › pc › gog › екшен": "⚔️",
    "ігри › pc › gog › екшен › hack and slash": "⚔️",
    "ігри › pc › gog › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › gog › екшен › шутери від третього": "🔫",
    "ігри › pc › gog › пригоди": "🗺️",
    "ігри › pc › gog › пригоди › walking-sim": "🚶",
    "ігри › pc › gog › пригоди › відкритий світ": "🌍",
    "ігри › pc › gog › пригоди › візуальні романи": "💌",
    "ігри › pc › gog › пригоди › квести": "🗝️",
    "ігри › pc › gog › симулятори": "🚜",
    "ігри › pc › gog › симулятори › будівельні": "🏗️",
    "ігри › pc › gog › симулятори › життя": "🏠",
    "ігри › pc › gog › симулятори › спорт": "🏅",
    "ігри › pc › gog › симулятори › транспорт": "🚘",
    "ігри › pc › gog › симулятори › фермерські": "🚜",
    "ігри › pc › gog › стратегії": "♟️",
    "ігри › pc › gog › стратегії › 4x": "🌌",
    "ігри › pc › gog › стратегії › rts": "🗺️",
    "ігри › pc › gog › стратегії › tower defense": "🏰",
    "ігри › pc › gog › стратегії › карткові": "🃏",
    "ігри › pc › gog › стратегії › покрокові": "🥇",
    "ігри › pc › gog › хорор": "👻",
    "ігри › pc › gog › хорор › виживання": "⛺",
    "ігри › pc › gog › хорор › кооперативний": "🤝",
    "ігри › pc › gog › хорор › психологічний": "🧠",
    "ігри › pc › gog › інді": "🎨",
    "ігри › pc › microsoft store": "🛒",
    "ігри › pc › steam › rpg": "🧙",
    "ігри › pc › steam › rpg › action-rpg": "🗡️",
    "ігри › pc › steam › rpg › crpg": "🛡️",
    "ігри › pc › steam › rpg › jrpg": "🧝",
    "ігри › pc › steam › rpg › mmorpg": "👑",
    "ігри › pc › steam › rpg › roguelike": "🎲",
    "ігри › pc › steam › виживання й крафт": "🏕️",
    "ігри › pc › steam › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › steam › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › steam › головоломки й казуальні": "🧩",
    "ігри › pc › steam › гонки й спорт": "🏁",
    "ігри › pc › steam › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › steam › гонки й спорт › симулятори": "🚜",
    "ігри › pc › steam › гонки й спорт › файтинги": "🥊",
    "ігри › pc › steam › гонки й спорт › футбол": "⚽",
    "ігри › pc › steam › для дітей і родини": "🧸",
    "ігри › pc › steam › екшен": "⚔️",
    "ігри › pc › steam › екшен › hack and slash": "⚔️",
    "ігри › pc › steam › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › steam › екшен › шутери від третього": "🔫",
    "ігри › pc › steam › пригоди": "🗺️",
    "ігри › pc › steam › пригоди › walking-sim": "🚶",
    "ігри › pc › steam › пригоди › відкритий світ": "🌍",
    "ігри › pc › steam › пригоди › візуальні романи": "💌",
    "ігри › pc › steam › пригоди › квести": "🗝️",
    "ігри › pc › steam › симулятори": "🚜",
    "ігри › pc › steam › симулятори › будівельні": "🏗️",
    "ігри › pc › steam › симулятори › життя": "🏠",
    "ігри › pc › steam › симулятори › спорт": "🏅",
    "ігри › pc › steam › симулятори › транспорт": "🚘",
    "ігри › pc › steam › симулятори › фермерські": "🚜",
    "ігри › pc › steam › стратегії": "♟️",
    "ігри › pc › steam › стратегії › 4x": "🌌",
    "ігри › pc › steam › стратегії › rts": "🗺️",
    "ігри › pc › steam › стратегії › tower defense": "🏰",
    "ігри › pc › steam › стратегії › карткові": "🃏",
    "ігри › pc › steam › стратегії › покрокові": "🥇",
    "ігри › pc › steam › хорор": "👻",
    "ігри › pc › steam › хорор › виживання": "⛺",
    "ігри › pc › steam › хорор › кооперативний": "🤝",
    "ігри › pc › steam › хорор › психологічний": "🧠",
    "ігри › pc › steam › інді": "🎨",
    "ігри › pc › ubisoft": "🗼",
    "ігри › pc › ubisoft › rpg": "🧙",
    "ігри › pc › ubisoft › rpg › action-rpg": "🗡️",
    "ігри › pc › ubisoft › rpg › crpg": "🛡️",
    "ігри › pc › ubisoft › rpg › jrpg": "🧝",
    "ігри › pc › ubisoft › rpg › mmorpg": "👑",
    "ігри › pc › ubisoft › rpg › roguelike": "🎲",
    "ігри › pc › ubisoft › виживання й крафт": "🏕️",
    "ігри › pc › ubisoft › виживання й крафт › відкриті світи": "🌍",
    "ігри › pc › ubisoft › виживання й крафт › пісочниці": "🏖️",
    "ігри › pc › ubisoft › головоломки й казуальні": "🧩",
    "ігри › pc › ubisoft › гонки й спорт": "🏁",
    "ігри › pc › ubisoft › гонки й спорт › аркадні": "🕹️",
    "ігри › pc › ubisoft › гонки й спорт › симулятори": "🚜",
    "ігри › pc › ubisoft › гонки й спорт › файтинги": "🥊",
    "ігри › pc › ubisoft › гонки й спорт › футбол": "⚽",
    "ігри › pc › ubisoft › для дітей і родини": "🧸",
    "ігри › pc › ubisoft › екшен": "⚔️",
    "ігри › pc › ubisoft › екшен › hack and slash": "⚔️",
    "ігри › pc › ubisoft › екшен › шутери від першого обличчя": "🎯",
    "ігри › pc › ubisoft › екшен › шутери від третього": "🔫",
    "ігри › pc › ubisoft › пригоди": "🗺️",
    "ігри › pc › ubisoft › пригоди › walking-sim": "🚶",
    "ігри › pc › ubisoft › пригоди › відкритий світ": "🌍",
    "ігри › pc › ubisoft › пригоди › візуальні романи": "💌",
    "ігри › pc › ubisoft › пригоди › квести": "🗝️",
    "ігри › pc › ubisoft › симулятори": "🚜",
    "ігри › pc › ubisoft › симулятори › будівельні": "🏗️",
    "ігри › pc › ubisoft › симулятори › життя": "🏠",
    "ігри › pc › ubisoft › симулятори › спорт": "🏅",
    "ігри › pc › ubisoft › симулятори › транспорт": "🚘",
    "ігри › pc › ubisoft › симулятори › фермерські": "🚜",
    "ігри › pc › ubisoft › стратегії": "♟️",
    "ігри › pc › ubisoft › стратегії › 4x": "🌌",
    "ігри › pc › ubisoft › стратегії › rts": "🗺️",
    "ігри › pc › ubisoft › стратегії › tower defense": "🏰",
    "ігри › pc › ubisoft › стратегії › карткові": "🃏",
    "ігри › pc › ubisoft › стратегії › покрокові": "🥇",
    "ігри › pc › ubisoft › хорор": "👻",
    "ігри › pc › ubisoft › хорор › виживання": "⛺",
    "ігри › pc › ubisoft › хорор › кооперативний": "🤝",
    "ігри › pc › ubisoft › хорор › психологічний": "🧠",
    "ігри › pc › ubisoft › інді": "🎨",
    "ігри › додатки": "🧩",
    "ігри › додатки › dlc й доповнення": "📦",
    "ігри › додатки › передзамовлення": "⏳",
    "ігри › додатки › ігрові предмети й паки": "🎒",
    "ігри › консолі й мобільні › playstation": "🎮",
    "ігри › консолі й мобільні › playstation › ps vr": "🥽",
    "ігри › консолі й мобільні › playstation › ps4": "🎮",
    "ігри › консолі й мобільні › playstation › ps5": "🏆",
    "ігри › консолі й мобільні › xbox": "🏆",
    "ігри › консолі й мобільні › xbox › pc game pass": "💻",
    "ігри › консолі й мобільні › xbox › series x/s": "🏆",
    "ігри › консолі й мобільні › мобільні": "📱",
    "ігрові проєкти й ассети › 2d-графіка": "🎨",
    "ігрові проєкти й ассети › 2d-графіка › спрайти й анімації персонажів": "🎞️",
    "ігрові проєкти й ассети › 2d-графіка › тайлсети": "🧱",
    "ігрові проєкти й ассети › 2d-графіка › фони й локації": "🏞️",
    "ігрові проєкти й ассети › 2d-графіка › шрифти": "🔤",
    "ігрові проєкти й ассети › 2d-графіка › інтерфейс (ui)": "📱",
    "ігрові проєкти й ассети › 2d-графіка › інтерфейс (ui) › hud": "🎮",
    "ігрові проєкти й ассети › 2d-графіка › інтерфейс (ui) › кнопки": "🔘",
    "ігрові проєкти й ассети › 2d-графіка › інтерфейс (ui) › меню": "📋",
    "ігрові проєкти й ассети › 2d-графіка › інтерфейс (ui) › іконки": "🔣",
    "ігрові проєкти й ассети › 2d-ресурси": "🖼️",
    "ігрові проєкти й ассети › 2d-ресурси › іконки": "🔣",
    "ігрові проєкти й ассети › 3d-моделі": "🧊",
    "ігрові проєкти й ассети › 3d-моделі › props": "📦",
    "ігрові проєкти й ассети › 3d-моделі › анімації й риги": "🤸",
    "ігрові проєкти й ассети › 3d-моделі › архітектура й будівлі": "🏛️",
    "ігрові проєкти й ассети › 3d-моделі › зброя й спорядження": "⚔️",
    "ігрові проєкти й ассети › 3d-моделі › персонажі": "👤",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › sci-fi": "👽",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › монстри й істоти": "👹",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › реалістичні": "👤",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › стилізовані": "🎭",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › стилізовані › anime": "🌸",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › стилізовані › cartoon": "🤡",
    "ігрові проєкти й ассети › 3d-моделі › персонажі › фентезі": "🧙",
    "ігрові проєкти й ассети › 3d-моделі › природа": "🌳",
    "ігрові проєкти й ассети › 3d-моделі › природа › дерева": "🌳",
    "ігрові проєкти й ассети › 3d-моделі › природа › рослини": "🌱",
    "ігрові проєкти й ассети › 3d-моделі › природа › скелі": "🏔️",
    "ігрові проєкти й ассети › 3d-моделі › транспорт": "🚗",
    "ігрові проєкти й ассети › 3d-моделі › транспорт › водний": "🚤",
    "ігрові проєкти й ассети › 3d-моделі › транспорт › космічний": "🚀",
    "ігрові проєкти й ассети › 3d-моделі › транспорт › наземний": "🚗",
    "ігрові проєкти й ассети › 3d-моделі › транспорт › повітряний": "✈️",
    "ігрові проєкти й ассети › звук і музика": "🎵",
    "ігрові проєкти й ассети › звук і музика › sfx": "🔊",
    "ігрові проєкти й ассети › звук і музика › амбієнт": "🌧️",
    "ігрові проєкти й ассети › звук і музика › голоси й озвучка": "🎙️",
    "ігрові проєкти й ассети › звук і музика › саундтреки": "🎵",
    "ігрові проєкти й ассети › інше › готові ігрові проєкти й вихідний код": "💻",
    "ігрові проєкти й ассети › інше › моди": "🔧",
    "ігрові проєкти й ассети › інше › моди › gta": "🚗",
    "ігрові проєкти й ассети › інше › моди › minecraft": "🧱",
    "ігрові проєкти й ассети › інше › моди › skyrim": "🏹",
    "ігрові проєкти й ассети › інше › моди › stardew valley": "🌾",
    "ігрові проєкти й ассети › інше › моди › terraria": "⛏️",
    "ігрові проєкти й ассети › інше › скрипти й плагіни": "⚙️",
    "ігрові проєкти й ассети › інше › скрипти й плагіни › бою": "⚔️",
    "ігрові проєкти й ассети › інше › скрипти й плагіни › діалогів": "💬",
    "ігрові проєкти й ассети › інше › скрипти й плагіни › збереження": "💾",
    "ігрові проєкти й ассети › інше › скрипти й плагіни › мережі": "🌐",
    "ігрові проєкти й ассети › інше › скрипти й плагіни › системи інвентарю": "🎒",
    "ігрові проєкти й ассети › інше › текстури й матеріали": "🎨",
    "ігрові проєкти й ассети › інше › текстури й матеріали › hdri": "🌅",
    "ігрові проєкти й ассети › інше › текстури й матеріали › pbr": "🎨",
    "ігрові проєкти й ассети › інше › текстури й матеріали › сканування": "🔍",
    "ігрові проєкти й ассети › інше › шаблони й кіти": "🧩",
    "ігрові проєкти й ассети › інше › шаблони й кіти › gamemaker": "🎮",
    "ігрові проєкти й ассети › інше › шаблони й кіти › godot": "🐸",
    "ігрові проєкти й ассети › інше › шаблони й кіти › roblox": "🧱",
    "ігрові проєкти й ассети › інше › шаблони й кіти › ui-кіти": "📱",
    "ігрові проєкти й ассети › інше › шаблони й кіти › unreal engine": "💻",
    "ігрові проєкти й ассети › інше › шейдери й vfx": "✨",
    "ігрові проєкти й ассети › інше › шейдери й vfx › вибухи": "💥",
    "ігрові проєкти й ассети › інше › шейдери й vfx › магія": "✨",
    "ігрові проєкти й ассети › інше › шейдери й vfx › погода": "🌦️",
  };
  let exactCategoryIcons;

  function categoryIcon(category) {
    const raw = category && typeof category === "object"
      ? category.path || category.category_path || category.name
      : category;
    const segments = String(raw || "").split("›").map((part) => part.trim()).filter(Boolean);
    const pathOverride = CATEGORY_PATH_ICON_OVERRIDES[segments.join(" › ").toLowerCase()];
    if (pathOverride) return pathOverride;
    const exactIcons = exactCategoryIcons || (exactCategoryIcons = {
      "Смартфони": "📱", "Планшети": "🔲", "Електронні книги": "📘",
      "Ноутбуки": "💻", "Настільні ПК": "🖥️", "Процесори": "⚙️",
      "Відеокарти": GPU_ICON, "Монітори": "🖥️", "Клавіатури": "⌨️",
      "Навушники": "🎧", "Колонки": "🔊", "Мікрофони": "🎙️",
      "Фотоапарати": "📷", "Об'єктиви": "🔭", "Екшн-камери": "📹",
      "Дрони": "🚁", "Телевізори": "📺", "Роботи-пилососи": "🤖",
      "Холодильники": "❄️", "Кавомашини": "☕", "Пральні й сушильні машини": "🧺",
      "Кросівки": "👟", "Черевики й чоботи": "🥾", "Туфлі й лофери": "👞",
      "Сукні": "👗", "Спідниці": "👗", "Сумки": "👜", "Рюкзаки": "🎒",
      "Годинники": "⌚", "Окуляри": "🕶️", "Сережки": "💎",
      "Меблі": "🛋️", "Дивани": "🛋️", "Ліжка": "🛏️", "Освітлення": "💡",
      "Ковдри й подушки": "🛏️", "Посуд": "🍽️", "Ручні інструменти": "🔧",
      "Електроінструмент": "🛠️", "Гантелі": "🏋️", "Тренажери": "🏋️",
      "Автокрісла": "🚗", "Кава й чай": "☕", "Конструктори": "🧱",
      "Для собак": "🐕", "Для котів": "🐈", "Книги": "📚",
      "Steam": "🎮", "EA Sports": "🎮", "Game Pass": "🎮",
      "Netflix": "▶️", "Windows": "💻", "Unity": "🧩",
      "Курси": "🎓", "Послуги": "🛠️",
      "Apple Watch": "⌚", "Android": "🤖", "iPad": "🔲", "Windows": "💻",
      "Чохли": "🛡️", "Стилуси": "✒️", "Клавіатури": "⌨️", "Підставки": "🗜️",
      "Материнські плати": "🔌", "ОЗП": "🧠", "SSD": "💾", "HDD": "💿",
      "Блоки живлення": "🔋", "Корпуси": "🖥️", "Охолодження": "❄️",
      "Вентилятори": "🌀", "Веб-камери": "📹", "Миша": "🖱️", "Миші": "🖱️",
      "Роутери": "📡", "Комутатори": "🔀", "Адаптери": "🔌",
      "Принтери": "🖨️", "Сканери": "📠", "МФУ": "🖨️",
      "Навушники": "🎧", "Мікрофони": "🎙️", "Плеєри": "▶️", "ЦАПи": "🎛️",
      "Об'єктиви": "🔭", "Штативи": "🔭", "Стабілізатори": "📷",
      "Камери спостереження": "📹", "Проектори": "📽️",
      "Пилососи": "🧹", "Парогенератори": "♨️", "Плити": "🍳",
      "Духовки": "🍲", "Мікрохвильовки": "⏲️", "Аерогрилі": "🍗",
      "Посудомийні машини": "🍽️", "Блендери": "🥤", "Міксери": "🥣",
      "Кондиціонери": "❄️", "Обігрівачі": "🔥", "Зволожувачі": "💧",
      "Очищувачі повітря": "🌬️", "Праски": "👔", "Фени": "💨",
      "Павербанки": "🔋", "Зарядки": "🔌", "Кабелі": "🔗",
      "UPS": "⚡", "Генератори": "⚡", "Сонячні панелі": "☀️",
      "VR": "🥽", "Геймпади": "🕹️", "Килимки": "🎮", "Стрім-деки": "🎥",
      "Костюми": "👔", "Штани": "👖", "Сорочки": "👔", "Купальники": "👙",
      "Валізи": "🧳", "Ремінці": "⌚", "Гаманці": "👛", "Головні убори": "🧢",
      "Прикраси": "💎", "Скатертини": "🧺", "Постільна": "🛏️",
      "Картини": "🖼️", "Свічки": "🕯️", "Зберігання": "📦",
      "Сантехніка": "🚿", "Електрика": "💡", "Будматеріали": "🧱",
      "Насіння": "🌱", "Добрива": "🌿", "Мангал": "🔥",
      "Фітнес": "🏋️", "Тренажери": "🏋️", "Гімнастика": "🤸",
      "Книги": "📚", "Канцелярія": "✏️", "Хобі": "🎨",
      "Іграшки": "🧸", "Корм": "🐾", "Косметика": "✨",
      "Краса й здоров'я": "💄", "Макіяж": "💄", "Туш": "💄",
      "Авто й мото": "🚗", "Автомобілі": "🚗", "Мотоцикли": "🏍️",
      "Ігрові проєкти й ассети": "💻", "Ігри": "🎮", "Steam": "🎮",
      "Навчання й контент": "📚", "Курси": "🎓", "Книги": "📚",
      "Смарт-годинники": "⌚", "Фітнес-браслети": "⌚", "Рації й радіостанції": "📻",
      "Електронні книги": "📘", "Графічні планшети": "✏️",
      "Процесори": "⚙️", "Відеокарти": GPU_ICON, "Материнські плати": "🔌",
      "ОЗП": "🧠", "Охолодження": "❄️", "Блоки живлення": "🔋",
      "Роутери": "📡", "Wi-Fi mesh": "📡", "Комутатори": "🔀", "Адаптери": "🔌",
      "Принтери й сканери": "🖨️", "3D-принтери": "🖨️", "МФУ": "🖨️",
      "Активні навушники": "🎧", "Бездротові TWS": "🎧",
      "Саундбари": "🔊", "Петлички": "🎙️", "Студійні мікрофони": "🎙️",
      "Вініл": "📀", "MP3": "🎵", "ЦАПи": "🎛️",
      "Дзеркальні фотоапарати": "📷", "Бездзеркальні фотоапарати": "📷",
      "Компакти": "📷", "Інстакс": "📷", "Екшн-камери": "📹",
      "Дрони": "🚁", "Штативи й стабілізатори": "🎥",
      "Телевізори": "📺", "Проектори": "📽️", "ТВ-приставки й медіаплеєри": "📺",
      "Роботи-пилососи": "🤖", "Вертикальні пилососи": "🧹", "Пилососи для дому": "🧹",
      "Парогенератори й пароочисники": "♨️", "Холодильники": "❄️",
      "Кавомашини": "☕", "Плити й духовки": "🍳", "Мікрохвильовки": "⏲️",
      "Мультипечі": "🍳", "Аерогрилі": "🍗", "Посудомийні машини": "🍽️",
      "Блендери": "🥤", "Міксери": "🥣", "Кухонні комбайни": "🥣",
      "Чайники": "☕", "Тостери": "🍞", "Сендвічниці": "🥪",
      "Кондиціонери": "❄️", "Обігрівачі": "🔥", "Зволожувачі": "💧",
      "Очищувачі повітря": "🌬️", "Вентилятори": "🌀",
      "Пральні й сушильні машини": "🧺", "Праски": "👔", "Відпарювачі": "👔",
      "Фени й стайлери": "💨", "Бритви й тримери": "🪒", "Епіляційні пристрої": "✨",
      "Розумні розетки": "🔌", "Розумні лампи": "💡", "Датчики": "📡",
      "Розумні замки": "🔐", "Розумні шторки": "🏠", "Термостати": "🌡️",
      "Павербанки": "🔋", "Зарядки": "🔌", "Кабелі": "🔗",
      "Джерела безперебійного живлення (UPS)": "⚡", "Електростанції": "⚡",
      "Сонячні панелі": "☀️", "Консолі": "🎮", "Геймпади й контролери": "🕹️",
      "VR/AR": "🥽", "Ігрові монітори": "🖥️", "Клавіатури": "⌨️",
      "Кросівки": "👟", "Черевики й чоботи": "🥾", "Туфлі й лофери": "👞",
      "Сандалі й шльопанці": "👡", "Домашнє взуття": "🥿",
      "Рюкзаки": "🎒", "Сумки": "👜", "Валізи й дорожні сумки": "🧳",
      "Годинники": "⌚", "Окуляри": "🕶️", "Ремені": "🧵", "Гаманці": "👛",
      "Головні убори": "🧢", "Шарфи й рукавиці": "🧣", "Прикраси": "💎",
      "Ліжка": "🛏️", "Матраци": "🛏️", "Шафи": "🚪", "Комоди": "🗄️",
      "Столи": "🪑", "Стільці": "🪑", "Крісла": "💺", "Журнальні столики": "🪵",
      "Освітлення": "💡", "Люстри": "💡", "Бра": "💡", "Торшери": "💡",
      "Декор": "🖼️", "Картини й постери": "🖼️", "Дзеркала": "👓",
      "Свічки": "🕯️", "Рамки": "🖼️", "Текстиль": "🧵",
      "Постільна білизна": "🛏️", "Ковдри й подушки": "🛏️", "Штори": "🏠",
      "Килими": "🧶", "Пледи": "🧣", "Каструлі й сковорідки": "🍳",
      "Ножі": "🔪", "Контейнери": "🧊", "Термоси": "🥤",
      "Ручні інструменти": "🔧", "Електроінструмент": "🛠️", "Акумуляторні": "🔋",
      "Вимірювальні": "📏", "Сантехніка": "🚿", "Електрика": "💡",
      "Будматеріали": "🧱", "Сад і город": "🌱", "Поливання": "💧",
      "Басейни й відпочинок": "🏖️", "Мангал": "🔥", "Насіння": "🌱",
      "Догляд за шкірою": "🧴", "Макіяж": "💄", "Волосся": "💇",
      "Парфумерія": "🧴", "Гігієна": "🦷", "Гантелі": "🏋️",
      "Автокрісла": "🚗", "Корм для собак": "🐕", "Корм для котів": "🐈",
    });

    for (let index = segments.length - 1; index >= 0; index -= 1) {
      const segment = segments[index].toLowerCase();
      const overrideEntry = Object.entries(CATEGORY_ICON_OVERRIDES).find(
        ([label]) => label.toLowerCase() === segment
      );
      if (overrideEntry) return overrideEntry[1];
      const exactEntry = Object.entries(exactCategoryIcons).find(
        ([label]) => label.toLowerCase() === segment
      );
      if (exactEntry) return exactEntry[1];
      for (const [needle, icon] of CATEGORY_ICON_RULES) {
        if (segment.startsWith(needle.toLowerCase())) return icon;
      }
    }
    return "🛍️";
  }

  window.MSCategoryIcons = Object.freeze({ iconFor: categoryIcon });

  function brandLogoUrl(brand) {
    const key = String(brand || "").toLowerCase();
    const brands = {
      "ea sports fc": "ea",
      "ea games": "ea",
      "ea app": "ea",
      "ea": "ea",
      "netflix": "netflix",
      "coursera": "coursera",
      "steam": "steam",
      "valve": "steam",
      "sony": "sony",
      "playstation": "playstation",
      "playstation store": "playstation",
      "google": "google",
      "google play": "googleplay",
      "apple": "apple",
      "samsung": "samsung",
      "nike": "nike",
      "unity": "unity",
      "figma": "figma",
      "django": "django",
      "python": "python",
      "nvidia": "nvidia",
      "amd": "amd",
      "intel": "intel",
      "roblox": "roblox",
    };
    for (const [name, slug] of Object.entries(brands)) {
      if (key === name) {
        return "https://cdn.jsdelivr.net/npm/simple-icons@16.38.1/icons/" + slug + ".svg";
      }
    }
    return "";
  }

  function categoryLogoUrl(product) {
    return brandLogoUrl(product.category_leaf);
  }

  function placeholderIconForProduct(product) {
    return categoryIcon(product.category_path || product.category_leaf);
  }

  function logCatalogDebugStats() {
    if (window.MS_DEBUG !== true || debugStatsLogged) return;
    debugStatsLogged = true;
    console.debug("[catalog] початкове завантаження", Object.assign({}, debugStats));
  }

  /* ---------- Картки товарів ---------- */
  function productCard(product) {
    const card = document.createElement("article");
    const soldOut = !product.in_stock;
    card.className = "product-card" + (soldOut ? " is-soldout" : "");
    card.dataset.productId = String(product.id);
    card.dataset.productPrice = String(product.price_card);
    card.dataset.productSignature = productSignature(product);
    const slug = String(product.slug || product.id || product.name);
    let hash = 0;
    for (let index = 0; index < slug.length; index += 1) {
      hash = (hash * 31 + slug.charCodeAt(index)) | 0;
    }
    const hue = Math.abs(hash) % 360;
    const productType = String(product.product_type || "").toLowerCase();
    const placeholderIcon = placeholderIconForProduct(product);
    const logoUrl = categoryLogoUrl(product);
    const logoMarkup = logoUrl
      ? '<img class="product-placeholder-logo" src="' + logoUrl + '" alt="">'
      : '<span class="product-placeholder-icon" aria-hidden="true">' + placeholderIcon + "</span>";
    const placeholder = '<div class="product-thumb product-placeholder" style="--placeholder-hue:' +
      hue + 'deg">' + logoMarkup +
      '<span class="product-placeholder-type">' +
      window.MS.escapeHtml(product.product_type_display) + "</span></div>";
    card.innerHTML =
      '<div class="product-thumb-wrap">' +
        (product.image
          ? '<img class="product-thumb" src="' + window.MS.escapeHtml(product.image) + '" alt="">'
          : placeholder) +
        '<span class="product-price">' + window.MS.escapeHtml(product.price_card) + " MC</span>" +
        '<button class="product-fav' + (product.is_favorite ? " is-on" : "") +
          '" type="button" aria-label="' + (product.is_favorite ? "Прибрати з обраного" : "Додати в обране") +
          '" aria-pressed="' + String(!!product.is_favorite) + '">' +
          (product.is_favorite ? "♥" : "♡") + "</button>" +
      "</div>" +
      '<div class="product-body">' +
        '<div class="product-name">' + window.MS.escapeHtml(product.name) + "</div>" +
        '<div class="product-meta"><span class="badge-soft is-muted">' +
          window.MS.escapeHtml(product.product_type_display) +
          (product.category_leaf ? " · " + window.MS.escapeHtml(product.category_leaf) : "") + "</span>" +
          (soldOut ? '<span class="badge-soft is-blood">Закінчився</span>' : "") +
        "</div>" +
        '<div class="product-foot">' +
          '<span class="small text-muted-soft product-seller" title="' +
            window.MS.escapeHtml(product.seller) + '">' + window.MS.escapeHtml(product.seller) + "</span>" +
          '<a class="btn btn-sm ' + (soldOut ? "btn-neutral" : "btn-primary") +
            '" href="/pages/product.html?id=' + product.id + '">Переглянути</a>' +
        "</div>" +
      "</div>";

    const image = card.querySelector("img.product-thumb");
    if (image) {
        image.addEventListener("error", () => {
          const fallback = document.createElement("div");
          fallback.className = "product-thumb product-placeholder";
          fallback.style.setProperty("--placeholder-hue", hue + "deg");
          fallback.innerHTML =
            logoMarkup +
            '<span class="product-placeholder-type">' +
            window.MS.escapeHtml(product.product_type_display) + "</span>";
          image.replaceWith(fallback);
        });
    }

    card.querySelectorAll(".product-placeholder-logo").forEach((logo) => {
      logo.addEventListener("error", () => {
        const fallback = document.createElement("span");
        fallback.className = "product-placeholder-icon";
        fallback.setAttribute("aria-hidden", "true");
        fallback.textContent = placeholderIcon;
        logo.replaceWith(fallback);
      });
    });

    const favBtn = card.querySelector(".product-fav");
    favBtn.addEventListener("click", () => toggleFavorite(product, favBtn));
    // Запам'ятовуємо стан каталогу й прокрутку, щоб «Назад» повернув усе як було
    const openLink = card.querySelector(".product-foot a");
    openLink.addEventListener("click", () => {
      saveScroll();
      rememberCatalogUrl();
    });
    return card;
  }

  async function toggleFavorite(product, btn) {
    if (!window.MSAuth.isAuthed()) {
      window.MS.toast("Увійдіть, щоб додати товар в обране.", "danger");
      return;
    }
    if (btn.disabled) return;
    btn.disabled = true;
    const wasFavorite = product.is_favorite;
    product.is_favorite = !wasFavorite;
    btn.classList.toggle("is-on", product.is_favorite);
    btn.classList.toggle("is-removing", wasFavorite);
    btn.setAttribute("aria-pressed", String(product.is_favorite));
    btn.setAttribute("aria-label", product.is_favorite ? "Прибрати з обраного" : "Додати в обране");
    btn.textContent = product.is_favorite ? "♥" : "♡";
    try {
      const res = await window.MS.post("/favorites", { product_id: product.id });
      const on = !!res.is_favorite;
      product.is_favorite = on;
      btn.classList.toggle("is-on", on);
      btn.classList.remove("is-removing");
      btn.setAttribute("aria-pressed", String(on));
      btn.setAttribute("aria-label", on ? "Прибрати з обраного" : "Додати в обране");
      btn.textContent = on ? "♥" : "♡";
      window.MS.toast(on ? "Додано в обране." : "Прибрано з обраного.", "success");
      if (window.MSCounters) window.MSCounters.refresh();
    } catch (err) {
      product.is_favorite = wasFavorite;
      btn.classList.toggle("is-on", wasFavorite);
      btn.classList.remove("is-removing");
      btn.setAttribute("aria-pressed", String(wasFavorite));
      btn.setAttribute("aria-label", wasFavorite ? "Прибрати з обраного" : "Додати в обране");
      btn.textContent = wasFavorite ? "♥" : "♡";
      window.MS.toast(err.message || "Не вдалося оновити обране.", "danger");
    } finally {
      btn.disabled = false;
    }
  }

  function makeHeartParticles(button) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (let index = 0; index < 7; index += 1) {
      const particle = document.createElement("span");
      const angle = (Math.PI * 2 * index) / 7;
      particle.className = "favorite-particle";
      particle.textContent = "♥";
      particle.style.setProperty("--heart-x", Math.cos(angle) * 28 + "px");
      particle.style.setProperty("--heart-y", Math.sin(angle) * 28 + "px");
      button.appendChild(particle);
      window.setTimeout(() => particle.remove(), 600);
    }
  }

  /* ---------- Пагінація ---------- */
  function renderPagination(page, pages) {
    els.pagination.innerHTML = "";
    els.pagination.hidden = pages <= 1;
    if (pages <= 1) return;

    if (window.matchMedia("(max-width: 575.98px)").matches) {
      const previous = document.createElement("button");
      previous.type = "button";
      previous.className = "btn btn-outline-primary btn-sm catalog-page-arrow";
      previous.textContent = "‹";
      previous.setAttribute("aria-label", "Попередня сторінка");
      previous.disabled = page <= 1;
      if (!previous.disabled) {
        previous.addEventListener("click", () => {
          state.page = page - 1;
          refresh();
        });
      }
      els.pagination.appendChild(previous);

      const jump = document.createElement("input");
      jump.type = "number";
      jump.className = "form-control form-control-sm catalog-page-jump";
      jump.min = "1";
      jump.max = String(pages);
      jump.value = String(page);
      jump.setAttribute("inputmode", "numeric");
      jump.setAttribute("aria-label", "Номер сторінки");

      const jumpToPage = () => {
        let requested = Number.parseInt(jump.value, 10);
        if (!Number.isFinite(requested) || requested < 1) requested = 1;
        if (requested > pages) requested = pages;
        jump.value = String(requested);
        if (requested !== page) {
          state.page = requested;
          refresh();
        }
      };
      jump.addEventListener("change", jumpToPage);
      jump.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          jumpToPage();
        }
      });
      els.pagination.appendChild(jump);

      const status = document.createElement("span");
      status.className = "catalog-page-status";
      status.textContent = " / " + pages;
      els.pagination.appendChild(status);

      const next = document.createElement("button");
      next.type = "button";
      next.className = "btn btn-outline-primary btn-sm catalog-page-arrow";
      next.textContent = "›";
      next.setAttribute("aria-label", "Наступна сторінка");
      next.disabled = page >= pages;
      if (!next.disabled) {
        next.addEventListener("click", () => {
          state.page = page + 1;
          refresh();
        });
      }
      els.pagination.appendChild(next);
      return;
    }

    const addButton = (label, target, disabled) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-outline-primary btn-sm";
      button.textContent = label;
      button.disabled = disabled;
      if (!disabled) {
        button.addEventListener("click", () => {
          state.page = target;
          refresh();
        });
      }
      els.pagination.appendChild(button);
    };

    addButton("← Назад", page - 1, page <= 1);
    const controls = document.createElement("div");
    controls.className = "pagination-controls";

    const status = document.createElement("span");
    status.className = "small text-muted-soft";
    status.textContent = "Сторінка " + page + " з " + pages;
    controls.appendChild(status);

    const pageInput = document.createElement("input");
    pageInput.type = "number";
    pageInput.className = "form-control form-control-sm catalog-page-input";
    pageInput.min = "1";
    pageInput.max = String(pages);
    pageInput.value = String(page);
    pageInput.setAttribute("aria-label", "Номер сторінки");
    controls.appendChild(pageInput);

    const jumpButton = document.createElement("button");
    jumpButton.type = "button";
    jumpButton.className = "btn btn-outline-primary btn-sm";
    jumpButton.textContent = "Перейти";
    const jumpToPage = () => {
      const requested = Number.parseInt(pageInput.value, 10);
      if (!Number.isInteger(requested) || requested < 1 || requested > pages) {
        pageInput.value = String(page);
        return;
      }
      if (requested === page) return;
      state.page = requested;
      refresh();
    };
    jumpButton.addEventListener("click", jumpToPage);
    pageInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        jumpToPage();
      }
    });
    controls.appendChild(jumpButton);
    els.pagination.appendChild(controls);

    addButton("Далі →", page + 1, page >= pages);
  }

  /* ---------- Список товарів ---------- */
  function renderProducts(data) {
    const results = data.results || [];
    els.productGrid.querySelectorAll(".skeleton-retry-error").forEach((item) => item.remove());
    const signature = productsSignature(data, results);
    if (signature === renderedProductsSignature) return false;

    const count = Number.isFinite(Number(data.count)) ? Number(data.count) : results.length;
    const countText = String(count);
    const countChanged = els.resultsCount.dataset.count !== countText;
    els.resultsCount.textContent = count + " шт.";
    if (countChanged) {
      els.resultsCount.dataset.count = countText;
      els.resultsCount.dataset.countSuffix = " шт.";
      delete els.resultsCount.dataset.countDone;
      if (window.MSMotion) window.MSMotion.count(els.resultsCount);
    }

    const existing = new Map(
      Array.from(els.productGrid.querySelectorAll(".product-card")).map((card) => [
        card.dataset.productId,
        card,
      ])
    );
    let nextPosition = els.productGrid.firstElementChild;
    results.forEach((product, index) => {
      const id = String(product.id);
      let card = existing.get(id);
      if (card) {
        existing.delete(id);
        if (card.dataset.productSignature !== productSignature(product)) {
          const previousPrice = card.dataset.productPrice;
          const updated = productCard(product);
          card.replaceChildren(...Array.from(updated.childNodes));
          card.classList.toggle("is-soldout", !product.in_stock);
          card.dataset.productPrice = String(product.price_card);
          card.dataset.productSignature = updated.dataset.productSignature;
          if (previousPrice !== String(product.price_card)) {
            card.querySelector(".product-price").classList.add("is-updated");
          }
        }
      } else {
        card = productCard(product);
        card.style.setProperty("--motion-delay", Math.min(index * 50, 600) + "ms");
      }
      if (card !== nextPosition) els.productGrid.insertBefore(card, nextPosition);
      nextPosition = card.nextElementSibling;
    });
    existing.forEach((card) => card.remove());
    els.productGrid.classList.remove("is-loading");
    els.productsEmpty.textContent = "Товарів не знайдено.";
    els.productsEmpty.hidden = results.length > 0;
    renderPagination(data.page || 1, data.pages || 1);
    renderedProductsSignature = signature;
    debugStats.render += 1;
    return true;
  }

  function cancelProductRequest() {
    productLoadSequence += 1;
    if (activeProductLoading) activeProductLoading.cancel();
    activeProductLoading = null;
    activeProductRequest = null;
  }

  function loadProducts() {
    debugStats.fetchProducts += 1;
    const requestUrl = "/products" + productsUrl();
    if (activeProductRequest && activeProductRequest.url === requestUrl) {
      return activeProductRequest.promise;
    }
    if (activeProductRequest) cancelProductRequest();

    const cacheKey = productsCacheKey(requestUrl);
    const cached = readProductsCache(cacheKey);
    els.productsEmpty.hidden = true;
    if (cached) {
      renderProducts(cached);
    }

    const sequence = ++productLoadSequence;
    const loading = cached ? null
      : window.MSSkeletons.showSkeleton(els.productGrid, "product", PRODUCT_PAGE_SIZE);
    activeProductLoading = loading;
    const request = (async () => {
      let data;
      try {
        data = await window.MS.get(requestUrl);
      } catch (err) {
        if (sequence !== productLoadSequence || !initialized) return;
        const message = window.MSSkeletons.friendlyErrorMessage(err, "Не вдалося завантажити товари.");
        const showError = () => {
          if (!cached) {
            els.productGrid.replaceChildren();
            renderedProductsSignature = null;
            els.resultsCount.textContent = "0";
            els.pagination.hidden = true;
          }
          els.productsEmpty.hidden = true;
          window.MSSkeletons.renderRetryError(
            els.productGrid,
            message,
            loadProducts,
            !!cached
          );
        };
        if (loading) await loading.finish(showError);
        else showError();
        return;
      }
      if (sequence !== productLoadSequence || !initialized) return;
      writeProductsCache(cacheKey, data);
      if (loading) await loading.finish(() => renderProducts(data));
      else renderProducts(data);
    })();
    const trackedRequest = request.finally(() => {
      if (activeProductRequest && activeProductRequest.sequence === sequence) {
        activeProductRequest = null;
      }
      if (activeProductLoading === loading) activeProductLoading = null;
    });
    activeProductRequest = { url: requestUrl, sequence: sequence, promise: trackedRequest };
    return trackedRequest;
  }

  /* ---------- Оновлення ---------- */
  async function refresh(options) {
    const opts = options || {};
    // 0 — прокрутити на початок; keepScroll — відновити збережену позицію («Назад»)
    scrollTarget = opts.keepScroll ? readSavedScroll() : 0;
    writeUrl(opts.replace);
    paintViews();
    await Promise.all([loadTiles(), loadSelection(), loadFilters(), loadProducts()]);
    applyScroll();
  }

  function syncForm() {
    els.searchInput.value = state.q;
    els.sortSelect.value = state.sort;
    els.availabilitySelect.value = state.availability;
  }

  function setControlsExpanded(expanded, animate) {
    window.clearTimeout(els.controlsHideTimer);
    if (expanded) {
      els.controls.hidden = false;
      els.controls.inert = false;
      if (animate) {
        els.controls.classList.add("is-collapsed");
        void els.controls.offsetHeight;
        els.controls.classList.remove("is-collapsed");
      } else {
        els.controls.classList.remove("is-collapsed");
      }
    } else {
      els.controls.hidden = false;
      els.controls.classList.add("is-collapsed");
      els.controls.inert = true;
      els.controlsHideTimer = window.setTimeout(() => {
        if (els.controls.classList.contains("is-collapsed")) els.controls.hidden = true;
      }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 120 : 320);
    }
    els.controls.setAttribute("aria-hidden", String(!expanded));
    els.controlsToggle.setAttribute("aria-expanded", String(expanded));
    els.controlsToggle.textContent = expanded
      ? "Згорнути пошук і розділи"
      : "Показати пошук і розділи";
  }

  function bind() {
    eventController = new AbortController();
    const options = { signal: eventController.signal };
    els.controlsToggle.addEventListener("click", () => {
      const expanded = els.controlsToggle.getAttribute("aria-expanded") === "true";
      setControlsExpanded(!expanded, true);
    }, options);
    els.searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      state.q = els.searchInput.value.trim();
      state.page = 1;
      refresh();
    }, options);
    els.sortSelect.addEventListener("change", () => {
      state.sort = els.sortSelect.value;
      state.page = 1;
      refresh();
    }, options);
    els.availabilitySelect.addEventListener("change", () => {
      state.availability = els.availabilitySelect.value;
      state.page = 1;
      refresh();
    }, options);
    els.clearBtn.addEventListener("click", () => {
      state.view = "";
      state.categories = [];
      state.q = "";
      state.sort = "time";
      state.availability = "active";
      state.page = 1;
      state.attrs = {};
      els.searchInput.value = "";
      els.sortSelect.value = "time";
      els.availabilitySelect.value = "active";
      refresh();
    }, options);
    window.addEventListener("popstate", () => {
      readUrl();
      syncForm();
      refresh({ replace: true, keepScroll: true });
    }, options);
  }

  function collectElements() {
    els.controls = $("catalogControls");
    els.controlsToggle = $("catalogControlsToggle");
    els.viewTabs = $("viewTabs");
    els.searchForm = $("searchForm");
    els.searchInput = $("searchInput");
    els.sortSelect = $("sortSelect");
    els.availabilitySelect = $("availabilitySelect");
    els.clearBtn = $("clearBtn");
    els.tileGrid = $("tileGrid");
    els.breadcrumbs = $("breadcrumbs");
    els.filtersPanel = $("filtersPanel");
    els.filtersList = $("filtersList");
    els.productGrid = $("productGrid");
    els.productsEmpty = $("productsEmpty");
    els.pagination = $("pagination");
    els.resultsCount = $("resultsCount");
  }

  function init(options) {
    if (initialized) return;
    collectElements();
    if (!els.controls || !els.controlsToggle || !els.productGrid) return;
    initialized = true;
    debugStats.init += 1;
    const sequence = ++initSequence;
    readUrl();
    syncForm();
    bind();

    if (!treeInitialized) {
      window.MSTree.init({
        onApply: (id, name, path, popupFilters) => addCategory(id, name, path, popupFilters),
        onClear: clearCategories,
      });
      treeInitialized = true;
    }
    if (options && options.restore) return;
    window.MSAuth.ready().then(() => {
      if (!initialized || sequence !== initSequence) return;
      return loadViews().then(() => {
        if (!initialized || sequence !== initSequence) return;
        return refresh({ replace: true, keepScroll: true });
      });
    }).then(() => {
      if (initialized && sequence === initSequence) logCatalogDebugStats();
    }).catch((error) => {
      if (initialized && sequence === initSequence) {
        console.error("Не вдалося ініціалізувати каталог.", error);
      }
    });
  }

  function cleanup() {
    if (!initialized) return;
    saveScroll();
    initialized = false;
    initSequence += 1;
    cancelProductRequest();
    if (eventController) {
      eventController.abort();
      eventController = null;
    }
  }

  window.addEventListener("pagehide", cleanup);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) init({ restore: true });
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init(), { once: true });
  } else {
    init();
  }
})();
