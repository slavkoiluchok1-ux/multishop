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
          '<span class="tile-icon" aria-hidden="true">' + categoryIcon(cat.name) + "</span>" +
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

  const CATEGORY_ICONS = [
    ["Смартфон", "📱"], ["Планшет", "🔲"], ["Ноутбук", "💻"], ["Комп'ют", "🖥️"],
    ["Відеокарт", "🎮"], ["Навушник", "🎧"], ["Колонк", "🔊"], ["Телевіз", "📺"],
    ["Фото", "📷"], ["Дрон", "🚁"], ["Консол", "🕹️"], ["Кросів", "👟"],
    ["Взут", "👞"], ["Одяг", "👕"], ["Сумк", "🎒"], ["Рюкзак", "🎒"],
    ["Мебл", "🛋️"], ["Крісл", "💺"], ["Ліжк", "🛏️"], ["Освіт", "💡"],
    ["Ламп", "💡"], ["Декор", "🖼️"], ["Кухн", "🍳"], ["Інструм", "🧰"],
    ["Сад", "🌱"], ["Косметик", "✨"], ["Макіяж", "💄"], ["Парфум", "🧴"],
    ["Спорт", "🏋️"], ["Гант", "🏋️"], ["Дитяч", "🧸"], ["Книга", "📚"],
    ["Конструктор", "🧱"], ["Кава", "☕"], ["Їжа", "🍽️"], ["Зоотовар", "🐾"],
    ["Гра", "🎮"], ["Steam", "🎮"], ["EA", "🎮"], ["Xbox", "🎮"],
    ["Netflix", "▶️"], ["Game Pass", "🎮"], ["Windows", "🪟"], ["Курс", "🎓"],
    ["Шаблон", "🎨"], ["Дизайн", "🎨"], ["Програмув", "💻"], ["Послуг", "🛠️"],
    ["Підписк", "♻️"], ["Подарун", "🎁"], ["Ліценз", "🔑"], ["Музик", "🎵"],
  ];

  function categoryIcon(name) {
    const text = String(name || "");
    const exactIcons = {
      "Смартфони": "📱", "Планшети": "🔲", "Електронні книги": "📘",
      "Ноутбуки": "💻", "Настільні ПК": "🖥️", "Процесори": "⚙️",
      "Відеокарти": "🎮", "Монітори": "🖥️", "Клавіатури": "⌨️",
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
      "Netflix": "▶️", "Windows": "🪟", "Unity": "🧩",
      "Курси": "🎓", "Послуги": "🛠️", "Підписки": "♻️",
      "Apple Watch": "⌚", "Android": "🤖", "iPad": "🔲", "Windows": "🪟",
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
    };
    for (const [name, icon] of Object.entries(exactIcons)) {
      if (text.toLowerCase().includes(name.toLowerCase())) return icon;
    }
    for (const [needle, icon] of CATEGORY_ICONS) {
      if (text.toLowerCase().includes(needle.toLowerCase())) return icon;
    }
    if (/Електрон/.test(text)) return "📱";
    if (/одяг|взут/.test(text)) return "👟";
    if (/меб|дім/.test(text)) return "🏠";
    return "▣";
  }

  function brandLogoUrl(brand) {
    const key = String(brand || "").toLowerCase();
    const brands = {
      "ea sports fc": "easports",
      "ea games": "easports",
      "ea": "ea",
      "netflix": "netflix",
      "coursera": "coursera",
      "microsoft": "microsoft",
      "xbox game pass": "xbox",
      "game pass": "xbox",
      "steam": "steam",
      "valve": "steam",
      "sony": "sony",
      "playstation": "playstation",
      "nintendo": "nintendo",
      "google": "google",
      "apple": "apple",
      "samsung": "samsung",
      "nike": "nike",
      "lego": "lego",
      "unity": "unity",
      "figma": "figma",
      "adobe": "adobe",
      "django": "django",
      "python": "python",
      "nvidia": "nvidia",
      "amd": "amd",
      "intel": "intel",
      "roblox": "roblox",
    };
    for (const [name, slug] of Object.entries(brands)) {
      if (key.includes(name)) {
        return "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/" + slug + ".svg";
      }
    }
    return "";
  }

  function categoryLogoUrl(product) {
    const text = (product.category_leaf + " " + product.name + " " + (product.brand || "")).toLowerCase();
    const logos = [
      ["ea sports", "ea.com"],
      ["ea games", "ea.com"],
      ["netflix", "netflix.com"],
      ["game pass", "xbox.com"],
      ["xbox", "xbox.com"],
      ["playstation", "playstation.com"],
      ["steam", "store.steampowered.com"],
      ["windows", "microsoft.com"],
      ["microsoft", "microsoft.com"],
      ["coursera", "coursera.org"],
      ["unity", "unity.com"],
      ["figma", "figma.com"],
      ["google", "google.com"],
      ["apple", "apple.com"],
      ["samsung", "samsung.com"],
      ["sony", "sony.com"],
      ["nvidia", "nvidia.com"],
      ["amd", "amd.com"],
      ["intel", "intel.com"],
      ["nike", "nike.com"],
      ["lego", "lego.com"],
    ];
    for (const [name, domain] of logos) {
      if (text.includes(name)) {
        return "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(domain) + "&sz=128";
      }
    }
    return brandLogoUrl(product.brand || product.name);
  }

  function placeholderIconForProduct(product) {
    const text = product.category_path + " " + product.name + " " + (product.brand || "");
    return categoryIcon(text);
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
