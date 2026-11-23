/* MultiShop — tree-popup.js
   Повноекранне вікно підтипів: панель характеристик зверху, підтипи нижче.
   Дані беруться з /api/catalog/categories/{id}/popup. Використовує MS (api.js). */
(function () {
  "use strict";

  function categoryIcon(name) {
    const text = String(name || "").toLowerCase();
    const rules = [
      ["смартф", "📱"], ["планш", "🔲"], ["ноутбук", "💻"], ["комп'ют", "🖥️"],
      ["відеокарт", "🎮"], ["навуш", "🎧"], ["колонк", "🔊"], ["телевіз", "📺"],
      ["фото", "📷"], ["дрон", "🚁"], ["консол", "🕹️"], ["кросів", "👟"],
      ["взут", "👞"], ["одяг", "👕"], ["сумк", "🎒"], ["рюкзак", "🎒"],
      ["мебл", "🛋️"], ["крісл", "💺"], ["ліжк", "🛏️"], ["освіт", "💡"],
      ["ламп", "💡"], ["декор", "🖼️"], ["кухн", "🍳"], ["інструм", "🧰"],
      ["сад", "🌱"], ["косметик", "✨"], ["макіяж", "💄"], ["парфум", "🧴"],
      ["спорт", "🏋️"], ["гант", "🏋️"], ["дитяч", "🧸"], ["книг", "📚"],
      ["конструкт", "🧱"], ["кава", "☕"], ["їжа", "🍽️"], ["зоотовар", "🐾"],
      ["гра", "🎮"], ["steam", "🎮"], ["netflix", "▶️"], ["xbox", "🎮"],
      ["підписк", "♻️"], ["гейм", "🎮"], ["курс", "🎓"], ["дизайн", "🎨"],
      ["послуг", "🛠️"], ["ліценз", "🔑"], ["музык", "🎵"],
    ];
    for (const [needle, icon] of rules) {
      if (text.includes(needle)) return icon;
    }
    if (text.includes("електрон")) return "📱";
    if (text.includes("одяг") || text.includes("взут")) return "👟";
    if (text.includes("меб") || text.includes("дім")) return "🏠";
    return "▣";
  }

  const state = {
    view: "",
    availability: "active",
    categoryId: null,
    categoryName: "",
    categoryPath: "",
    attributes: [],
    filters: {},
    onApply: null,
    onClear: null,
    ready: false,
  };

  function el(id) {
    return document.getElementById(id);
  }

  function renderBreadcrumbs(crumbs) {
    const wrap = el("twBreadcrumbs");
    wrap.innerHTML = "";
    crumbs.forEach((crumb, i) => {
      if (i) {
        const sep = document.createElement("span");
        sep.className = "crumbs-sep";
        sep.textContent = "›";
        wrap.appendChild(sep);
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = crumb.name;
      btn.addEventListener("click", () => openCategory(crumb.id, state.view, state.availability));
      wrap.appendChild(btn);
    });
  }

  function renderAttributes(attributes) {
    const box = el("twAttrs");
    box.innerHTML = "";
    if (!attributes.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const title = document.createElement("h3");
    title.textContent = "Характеристики розділу";
    box.appendChild(title);
    attributes.forEach((attr) => {
      const row = document.createElement("div");
      row.className = "attr-row";
      const name = document.createElement("div");
      name.className = "attr-name";
      name.textContent = attr.name + (attr.unit ? ", " + attr.unit : "") + (attr.required ? " (обов'язкова)" : "");
      row.appendChild(name);
      const values = document.createElement("div");
      values.className = "filter-chips";
      const selected = state.filters[String(attr.id)] || [];
      const mode = attr.kind === "multiple" ? "multiple" : "single";
      const addChip = (label, value) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "filter-chip" + (selected.indexOf(value) !== -1 ? " is-on" : "");
        chip.textContent = label;
        chip.setAttribute("aria-pressed", selected.indexOf(value) !== -1 ? "true" : "false");
        chip.addEventListener("click", () => toggleFilter(attr.id, value, mode));
        values.appendChild(chip);
      };

      if (attr.kind === "number") {
        const current = selected.find((value) => value.indexOf("..") !== -1) || "";
        const parts = current.split("..");
        const range = document.createElement("div");
        range.className = "filter-range";
        range.innerHTML =
          '<input type="number" class="form-control form-control-sm" aria-label="' +
          window.MS.escapeHtml("Мінімальне значення: " + attr.name) +
          '" placeholder="від" value="' + window.MS.escapeHtml(parts[0] || "") + '">' +
          "<span>—</span>" +
          '<input type="number" class="form-control form-control-sm" aria-label="' +
          window.MS.escapeHtml("Максимальне значення: " + attr.name) +
          '" placeholder="до" value="' + window.MS.escapeHtml(parts[1] || "") + '">' +
          '<button type="button" class="btn btn-outline-primary btn-sm">ОК</button>';
        const inputs = range.querySelectorAll("input");
        const applyRange = () => {
          if (inputs[0].value === "" && inputs[1].value === "") {
            setFilter(attr.id, []);
          } else {
            setFilter(attr.id, [inputs[0].value + ".." + inputs[1].value]);
          }
        };
        range.querySelector("button").addEventListener("click", applyRange);
        inputs.forEach((input) => input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            applyRange();
          }
        }));
        values.appendChild(range);
      } else {
        let options = (attr.options || []).map((option) => option.value);
        if (attr.kind === "bool") options = ["так", "ні"];
        options.forEach((value) => addChip(value, value));
      }

      addChip("не вказано", "");
      row.appendChild(values);
      box.appendChild(row);
    });
  }

  function toggleFilter(id, value, mode) {
    const key = String(id);
    const selected = state.filters[key] || [];
    if (value === "") {
      state.filters[key] = selected.length === 1 && selected[0] === "" ? [] : [""];
    } else if (mode === "single") {
      state.filters[key] = selected.length === 1 && selected[0] === value ? [] : [value];
    } else {
      const values = selected.filter((item) => item !== "");
      const index = values.indexOf(value);
      if (index === -1) values.push(value);
      else values.splice(index, 1);
      state.filters[key] = values;
    }
    if (!state.filters[key].length) delete state.filters[key];
    renderAttributes(state.attributes);
  }

  function setFilter(id, values) {
    const key = String(id);
    if (values.length) state.filters[key] = values;
    else delete state.filters[key];
    renderAttributes(state.attributes);
  }

  function renderChildren(children) {
    const grid = el("twChildren");
    grid.innerHTML = "";
    if (!children.length) {
      grid.hidden = true;
      el("twChildrenHeading").hidden = true;
      return;
    }
    grid.hidden = false;
    el("twChildrenHeading").hidden = false;
    children.forEach((child) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "tile";
      tile.innerHTML =
        '<span class="tile-icon" aria-hidden="true">' + categoryIcon(child.name) + "</span>" +
        '<span class="tile-name">' + window.MS.escapeHtml(child.name) + "</span>";
      tile.addEventListener("click", () => {
        openCategory(child.id, state.view, state.availability);
      });
      grid.appendChild(tile);
    });
  }

  async function openCategory(id, view, availability) {
    const wasOpen = !el("treeWindow").hidden;
    state.view = view !== undefined && view !== null && view !== "" ? view : state.view;
    if (availability !== undefined && availability !== null && availability !== "") {
      state.availability = availability;
    }
    let data;
    try {
      const p = new URLSearchParams();
      if (state.view) p.set("view", state.view);
      p.set("availability", state.availability);
      data = await window.MS.get("/catalog/categories/" + id + "/popup?" + p.toString());
    } catch (err) {
      window.MS.toast(err.message || "Не вдалося відкрити розділ.", "danger");
      return;
    }
    if (!wasOpen) state.filters = {};
    state.categoryId = id;
    state.categoryName = data.category.name;
    state.categoryPath = data.category.path;
    state.attributes = data.attributes || [];
    const available = new Set(state.attributes.map((attribute) => String(attribute.id)));
    Object.keys(state.filters).forEach((key) => {
      if (!available.has(key)) delete state.filters[key];
    });
    el("twTitle").textContent = data.category.name;
    renderBreadcrumbs(data.breadcrumbs || []);
    renderAttributes(state.attributes);
    renderChildren((data.children || []).filter((child) => Number(child.product_count) > 0));
    el("treeWindow").hidden = false;
  }

  function apply(id, name, path) {
    if (state.onApply) state.onApply(id, name, path, state.filters);
    close();
  }

  function close() {
    el("treeWindow").hidden = true;
  }

  function init(options) {
    if (state.ready) return;
    state.onApply = options.onApply;
    state.onClear = options.onClear;
    el("twClose").addEventListener("click", close);
    el("treeWindow").addEventListener("click", (event) => {
      if (event.target.id === "treeWindow") close();
    });
    el("twApply").addEventListener("click", () => {
      if (state.categoryId) apply(state.categoryId, state.categoryName, state.categoryPath);
    });
    el("twClear").addEventListener("click", () => {
      if (state.onClear) state.onClear();
      close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
    state.ready = true;
  }

  window.MSTree = { init: init, openCategory: openCategory, close: close };
})();
