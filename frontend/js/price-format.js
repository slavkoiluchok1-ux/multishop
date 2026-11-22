/* MultiShop — price-format.js
   Форматування цін у MultiCoins (MC). Гроші ніколи не float — працюємо з рядками. */
(function () {
  "use strict";

  const DECIMALS = 6;

  function toParts(value) {
    if (value === null || value === undefined || value === "") {
      return { sign: "", int: "0", frac: "" };
    }
    let raw = String(value).trim().replace(/\s+/g, "").replace(",", ".");
    let sign = "";
    if (raw.charAt(0) === "-") {
      sign = "-";
      raw = raw.slice(1);
    }
    const dot = raw.indexOf(".");
    let int = dot === -1 ? raw : raw.slice(0, dot);
    let frac = dot === -1 ? "" : raw.slice(dot + 1);
    int = int.replace(/^0+(?=\d)/, "") || "0";
    frac = (frac + "0".repeat(DECIMALS)).slice(0, DECIMALS);
    return { sign: sign, int: int, frac: frac };
  }

  /** Групує цілу частину: 1234567 -> "1 234 567". */
  function groupInt(int) {
    return String(int).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /** Повне значення з усіма знаками після коми. */
  function formatMC(value) {
    const p = toParts(value);
    return p.sign + groupInt(p.int) + (p.frac ? "." + p.frac : "");
  }

  /** Короткий показ: максимум 3 знаки після коми, зайві нулі прибрано. */
  function formatMCShort(value) {
    const p = toParts(value);
    // Спершу обмежуємо трьома знаками, потім прибираємо зайві нулі —
    // інакше «0.0005» дало б «0.000» замість «0».
    const frac = p.frac.slice(0, 3).replace(/0+$/, "");
    return p.sign + groupInt(p.int) + (frac ? "." + frac : "");
  }

  /** Гривнева оцінка за курсом (за замовчуванням 1 MC = 10 ₴). */
  function toUAH(value, rate) {
    const r = rate === undefined || rate === null ? 10 : rate;
    const p = toParts(value);
    const scaled = (Number(p.int + "." + p.frac) * Number(r)).toFixed(2);
    const sp = toParts(scaled);
    return sp.sign + groupInt(sp.int) + (sp.frac ? "." + sp.frac : "");
  }

  function formatUAH(value, rate) {
    return toUAH(value, rate) + " ₴";
  }

  window.MSPrice = {
    DECIMALS: DECIMALS,
    formatMC: formatMC,
    formatMCShort: formatMCShort,
    toUAH: toUAH,
    formatUAH: formatUAH,
  };
})();
