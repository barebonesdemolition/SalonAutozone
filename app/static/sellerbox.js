/* "Sold by" box on part and car pages: name, verified badge, rating, link to profile. */
(function (global) {
  'use strict';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function mount(el, sellerId) {
    if (!el || !sellerId) return;
    fetch('/api/sellers/' + encodeURIComponent(sellerId)).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
      if (!s) return;
      var verified = s.business && s.business.verified;
      var rating = s.reviews_count ? '<span style="color:var(--gold);font-size:16px">★</span> <b>' + s.rating + '</b> <span class="hint" style="display:inline">(' + s.reviews_count + ')</span>' : '<span class="hint" style="display:inline">No ratings yet</span>';
      el.innerHTML = '<a class="box" href="/seller/' + encodeURIComponent(s.id) + '" style="display:flex;gap:12px;align-items:center;text-decoration:none;color:inherit">' +
        '<span aria-hidden="true" style="width:44px;height:44px;border-radius:50%;background:var(--ink);color:var(--gold);font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0">' + esc((s.name || '?').charAt(0).toUpperCase()) + '</span>' +
        '<span style="flex:1;min-width:0"><span class="hint" style="margin:0;display:block">Sold by</span><b style="color:var(--ink)">' + esc(s.name) + '</b>' +
        (verified ? ' <span style="font-size:12px;font-weight:800;padding:2px 7px;border-radius:6px;background:var(--ok-50);color:var(--ok)">✓ Verified</span>' : '') +
        '<span style="display:block;font-size:14px">' + rating + (s.delivered_sales ? ' <span class="hint" style="display:inline">· ' + s.delivered_sales + ' delivered</span>' : '') + '</span></span>' +
        '<span aria-hidden="true" style="color:var(--muted);font-size:22px">›</span></a>';
    }).catch(function () {});
  }
  global.SAZSellerBox = { mount: mount };
})(window);
