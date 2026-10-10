/* SAZ Cart — localStorage-backed cart utility used across all pages. */
(function (global) {
  var KEY = "saz_cart";

  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]") || []; }
    catch (e) { return []; }
  }
  function write(items) {
    localStorage.setItem(KEY, JSON.stringify(items));
    document.dispatchEvent(new CustomEvent("saz-cart-change", { detail: items }));
  }
  function add(part, qty) {
    qty = Math.max(1, Math.min(20, Number(qty) || 1));
    var items = read();
    var existing = items.find(function (i) { return i.part_id === part.id; });
    if (existing) {
      existing.qty = Math.min(20, existing.qty + qty);
    } else {
      items.push({
        part_id: part.id,
        qty: qty,
        name: part.name,
        price_sll: Number(part.price_sll || 0),
        image_url: part.image_url || null
      });
    }
    write(items);
  }
  function remove(partId) {
    write(read().filter(function (i) { return i.part_id !== partId; }));
  }
  function setQty(partId, qty) {
    qty = Math.max(1, Math.min(20, Number(qty) || 1));
    var items = read();
    var it = items.find(function (i) { return i.part_id === partId; });
    if (it) { it.qty = qty; write(items); }
  }
  function clear() { write([]); }
  function count() { return read().reduce(function (s, i) { return s + i.qty; }, 0); }
  function total() { return read().reduce(function (s, i) { return s + i.qty * i.price_sll; }, 0); }
  function itemsParam() {
    return read().map(function (i) { return i.part_id + ":" + i.qty; }).join(",");
  }

  global.SAZCart = { read: read, add: add, remove: remove, setQty: setQty, clear: clear, count: count, total: total, itemsParam: itemsParam };

})(window);
