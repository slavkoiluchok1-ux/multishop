/* MultiShop — checkout confirmation and wallet payment. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dialog = $("checkoutDialog");
  if (!dialog) return;

  const form = $("checkoutForm");
  const errorBox = $("checkoutError");
  let cart = null;
  let user = null;
  let wallet = null;
  let hasPhysicalItems = false;
  let contactSequence = 0;

  function showError(message) {
    errorBox.textContent = message || "";
    errorBox.hidden = !message;
  }

  function addContactEntry(name, value) {
    contactSequence += 1;
    const row = document.createElement("div");
    row.className = "checkout-contact-entry";

    const nameGroup = document.createElement("div");
    nameGroup.className = "checkout-contact-field";
    const nameLabel = document.createElement("label");
    nameLabel.className = "form-label small";
    nameLabel.htmlFor = "checkoutContactName" + contactSequence;
    nameLabel.textContent = "Назва способу зв’язку";
    const nameInput = document.createElement("input");
    nameInput.className = "form-control";
    nameInput.id = nameLabel.htmlFor;
    nameInput.type = "text";
    nameInput.maxLength = 64;
    nameInput.placeholder = "Наприклад, Instagram";
    nameInput.dataset.contactLabel = "";
    nameInput.value = name || "";
    nameGroup.append(nameLabel, nameInput);

    const valueGroup = document.createElement("div");
    valueGroup.className = "checkout-contact-field";
    const valueLabel = document.createElement("label");
    valueLabel.className = "form-label small";
    valueLabel.htmlFor = "checkoutContactValue" + contactSequence;
    valueLabel.textContent = "Ваш контакт";
    const valueInput = document.createElement("input");
    valueInput.className = "form-control";
    valueInput.id = valueLabel.htmlFor;
    valueInput.type = "text";
    valueInput.maxLength = 255;
    valueInput.placeholder = "Нік, номер або посилання";
    valueInput.dataset.contactValue = "";
    valueInput.value = value || "";
    valueGroup.append(valueLabel, valueInput);

    const remove = document.createElement("button");
    remove.className = "btn btn-outline-danger btn-sm";
    remove.type = "button";
    remove.textContent = "Прибрати";
    remove.setAttribute("aria-label", "Прибрати додатковий контакт");
    remove.addEventListener("click", () => row.remove());

    row.append(nameGroup, valueGroup, remove);
    $("checkoutOtherContacts").appendChild(row);
  }

  function readOtherContacts() {
    const rows = Array.from($("checkoutOtherContacts").querySelectorAll(".checkout-contact-entry"));
    const contacts = [];
    for (const row of rows) {
      const label = row.querySelector("[data-contact-label]").value.trim();
      const value = row.querySelector("[data-contact-value]").value.trim();
      if (label || value) {
        if (!label || !value) {
          showError("Заповніть назву й дані контакту або приберіть порожній рядок.");
          row.querySelector(label ? "[data-contact-value]" : "[data-contact-label]").focus();
          return { valid: false, value: "" };
        }
        contacts.push(label + ": " + value);
      }
    }
    return { valid: true, value: contacts.join("\n") };
  }

  function fillForm(profileData) {
    const profile = profileData.profile || {};
    const delivery = profile.autofill_delivery ? profile : {};
    $("checkoutEmail").value = profileData.email || "";
    $("checkoutPhone").value = profile.phone || "";
    $("checkoutTelegram").value = profile.telegram || "";
    $("checkoutOtherContacts").replaceChildren();
    [
      ["Instagram", profile.instagram],
      ["Viber", profile.viber],
      ["Facebook", profile.facebook],
      ["WhatsApp", profile.whatsapp],
    ].filter((entry) => String(entry[1] || "").trim())
      .forEach((entry) => addContactEntry(entry[0], entry[1]));
    $("checkoutRecipient").value = delivery.recipient_name || profileData.username || "";
    $("checkoutCountry").value = delivery.delivery_country || "";
    $("checkoutCity").value = delivery.delivery_city || "";
    $("checkoutPostcode").value = delivery.delivery_postcode || "";
    $("checkoutStreet").value = delivery.delivery_street || "";
  }

  async function openDialog() {
    showError("");
    try {
      [cart, wallet] = await Promise.all([
        window.MS.get("/cart"),
        window.MS.get("/wallet"),
      ]);
      if (!cart.items || !cart.items.length) {
        throw new Error("Кошик порожній.");
      }
      hasPhysicalItems = cart.items.some((item) => item.product_type === "physical");
      $("checkoutDeliveryFields").hidden = !hasPhysicalItems;
      $("checkoutStepOne").hidden = false;
      $("checkoutStepTwo").hidden = true;
      $("checkoutStepLabel").textContent = "Крок 1 із 2";
      $("checkoutTitle").textContent = "Контакти й доставка";
      dialog.showModal();
    } catch (error) {
      window.MS.toast(error.message || "Не вдалося підготувати оформлення.", "danger");
    }
  }

  function showPaymentStep() {
    if (!form.reportValidity()) return;
    const otherContacts = readOtherContacts();
    if (!otherContacts.valid) return;
    if (!($("checkoutPhone").value.trim() || $("checkoutTelegram").value.trim() ||
      $("checkoutEmail").value.trim() || otherContacts.value)) {
      showError("Укажіть хоча б один контакт.");
      return;
    }
    if (hasPhysicalItems && !(
      $("checkoutCountry").value.trim() &&
      $("checkoutCity").value.trim() &&
      $("checkoutStreet").value.trim()
    )) {
      showError("Для фізичного товару заповніть країну, місто й адресу.");
      return;
    }
    showError("");
    $("checkoutStepOne").hidden = true;
    $("checkoutStepTwo").hidden = false;
    $("checkoutStepLabel").textContent = "Крок 2 із 2";
    $("checkoutTitle").textContent = "Перевірка й оплата";
    $("checkoutTotal").textContent = (cart.total_full || cart.total || "0") + " MC";
    const balance = wallet.balance || {};
    $("checkoutBalance").textContent =
      (window.MSPrice ? window.MSPrice.formatMCShort(balance.free_balance_mc) : balance.free_balance_mc) + " MC";
    if (Number(balance.free_balance_mc) < Number(cart.total)) {
      showError("Недостатньо вільних MC для оплати цього кошика.");
      $("checkoutSubmit").disabled = true;
    } else {
      $("checkoutSubmit").disabled = false;
    }
  }

  async function submitCheckout(event) {
    event.preventDefault();
    showError("");
    const otherContacts = readOtherContacts();
    if (!otherContacts.valid) return;
    const submit = $("checkoutSubmit");
    submit.disabled = true;
    submit.textContent = "Оформлюємо…";
    const data = {
      contact_email: $("checkoutEmail").value.trim(),
      contact_phone: $("checkoutPhone").value.trim(),
      contact_telegram: $("checkoutTelegram").value.trim(),
      contact_other: otherContacts.value,
      order_comment: $("checkoutComment").value.trim(),
      recipient_name: $("checkoutRecipient").value.trim(),
      delivery_address: {
        country: $("checkoutCountry").value.trim(),
        city: $("checkoutCity").value.trim(),
        postcode: $("checkoutPostcode").value.trim(),
        street: $("checkoutStreet").value.trim(),
        recipient_name: $("checkoutRecipient").value.trim(),
      },
    };
    try {
      const result = await window.MS.post("/checkout", data);
      window.dispatchEvent(new Event("ms:wallet-changed"));
      dialog.close();
      if (window.MSCounters) window.MSCounters.refresh();
      window.location.href = "/pages/orders.html?checkout=" +
        encodeURIComponent(result.orders.map((order) => order.id).join(","));
    } catch (error) {
      const fields = error.payload && error.payload.fields;
      const firstField = fields && Object.keys(fields)[0];
      const message = firstField
        ? (Array.isArray(fields[firstField]) ? fields[firstField].join(" ") : fields[firstField])
        : error.message;
      showError(message || "Не вдалося оформити замовлення.");
      submit.disabled = false;
    } finally {
      submit.textContent = "Оплатити й оформити";
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const start = $("checkoutStart");
    const hint = $("checkoutHint");
    try {
      user = await window.MSAuth.ready();
      if (!user || !user.authenticated) {
        hint.textContent = "Увійдіть, щоб оформити покупку з балансу гаманця.";
        return;
      }
      start.disabled = false;
      hint.textContent = "Оплата відбувається з вільного балансу гаманця.";
      const [profile, cartState] = await Promise.all([
        window.MS.get("/profile"),
        window.MS.get("/cart"),
      ]);
      fillForm(profile);
      cart = cartState;
      start.addEventListener("click", openDialog);
    } catch (error) {
      hint.textContent = error.message || "Увійдіть, щоб оформити замовлення.";
    }

    $("checkoutClose").addEventListener("click", () => dialog.close());
    $("checkoutAddContact").addEventListener("click", () => addContactEntry("", ""));
    $("checkoutNext").addEventListener("click", showPaymentStep);
    $("checkoutBack").addEventListener("click", () => {
      showError("");
      $("checkoutStepOne").hidden = false;
      $("checkoutStepTwo").hidden = true;
      $("checkoutStepLabel").textContent = "Крок 1 із 2";
      $("checkoutTitle").textContent = "Контакти й доставка";
    });
    form.addEventListener("submit", submitCheckout);
  });
})();
