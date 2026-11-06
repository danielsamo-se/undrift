const bag = new Map();
const money = amount => new Intl.NumberFormat("en", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(amount);
const dialog = document.querySelector(".bag-dialog");
let toastTimer;
function renderBag() {
  const items = document.querySelector("[data-bag-items]");
  items.replaceChildren();
  let total = 0, count = 0;
  for (const [name, item] of bag) {
    const row = document.createElement("li");
    row.textContent = `${name} × ${item.count} — ${money(item.price * item.count)}`;
    const remove = document.createElement("button");
    remove.textContent = "Remove"; remove.setAttribute("aria-label", `Remove ${name}`);
    remove.onclick = () => { bag.delete(name); renderBag(); };
    row.append(remove); items.append(row); total += item.price * item.count; count += item.count;
  }
  if (!count) { const row = document.createElement("li"); row.textContent = "A little room for something good."; items.append(row); }
  document.querySelector("[data-bag-total]").textContent = money(total);
  document.querySelector("[data-bag-count]").textContent = count;
}
document.querySelectorAll("[data-add]").forEach(button => button.addEventListener("click", () => {
  const item = bag.get(button.dataset.add) || { price: Number(button.dataset.price), count: 0 };
  item.count++; bag.set(button.dataset.add, item); renderBag();
  const toast = document.querySelector(".toast"); toast.textContent = `${button.dataset.add} added to your bag`; toast.classList.add("visible");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("visible"), 2500);
}));
document.querySelector("[data-open-bag]")?.addEventListener("click", () => { renderBag(); dialog.showModal(); });
document.querySelectorAll("[data-close-bag]").forEach(button => button.addEventListener("click", () => dialog.close()));
dialog?.addEventListener("click", e => { if (e.target === dialog) dialog.close(); });
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll("[data-filter]").forEach(b => b.classList.toggle("active", b === button));
  document.querySelectorAll("[data-category]").forEach(card => { card.hidden = button.dataset.filter !== "all" && card.dataset.category !== button.dataset.filter; });
}));
