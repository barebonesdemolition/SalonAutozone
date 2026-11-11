/* Maps for SalonAutoZone: live delivery tracking and admin overviews.
   Leaflet + OpenStreetMap are loaded only when a map is shown. */
(function (global) {
  'use strict';
  var loading = null;
  function loadLeaflet() {
    if (global.L) return Promise.resolve(global.L);
    if (loading) return loading;
    loading = new Promise(function (ok, fail) {
      var css = document.createElement('link');
      css.rel = 'stylesheet'; css.href = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';
      document.head.appendChild(css);
      var js = document.createElement('script');
      js.src = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';
      js.onload = function () { ok(global.L); }; js.onerror = function () { loading = null; fail(new Error('map')); };
      document.head.appendChild(js);
    });
    return loading;
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function dot(L, color, label) {
    return L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: '<div style="width:30px;height:30px;border-radius:50%;background:' + color + ';color:#fff;border:3px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;font:700 14px system-ui">' + label + '</div>' });
  }
  function baseMap(L, el) {
    var map = L.map(el, { scrollWheelZoom: false }).setView([8.465, -13.23], 12);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    return map;
  }
  function ago(s) { return s == null ? '' : s < 60 ? 'just now' : Math.round(s / 60) + ' min ago'; }

  /* Live tracking for one order. opts: { el, info, orderId, token, every } */
  function track(opts) {
    var map = null, layers = [], timer = null, fitted = false;
    function draw(t) {
      if (!map) return;
      layers.forEach(function (l) { map.removeLayer(l); }); layers = [];
      var pts = [];
      function add(lat, lng, color, label, text) {
        if (lat == null || lng == null) return;
        var m = global.L.marker([lat, lng], { icon: dot(global.L, color, label) }).addTo(map).bindPopup(text);
        layers.push(m); pts.push([lat, lng]);
      }
      add(t.shop.lat, t.shop.lng, '#0e7c74', 'S', esc(t.shop.name || 'Shop'));
      add(t.buyer.lat, t.buyer.lng, '#c98a00', 'B', 'Delivery point');
      if (t.rider && t.rider.lat != null) add(t.rider.lat, t.rider.lng, '#d4432c', 'R', 'Rider ' + esc(t.rider.name || '') + ', ' + ago(t.rider.seen_s));
      if (t.delivered_at) add(t.delivered_at.lat, t.delivered_at.lng, '#2f8a3b', '✓', 'Handed over here');
      if (pts.length && !fitted) { map.fitBounds(pts, { padding: [40, 40], maxZoom: 16 }); fitted = true; }
      else if (t.rider && t.rider.lat != null) map.panTo([t.rider.lat, t.rider.lng]);
    }
    function words(t) {
      if (!opts.info) return;
      var r = t.rider;
      if (!r) { opts.info.textContent = t.status === 'delivered' ? 'Delivered.' : 'Waiting for a rider.'; return; }
      var stage = r.stage === 'picked_up' ? 'On the way to you' : 'Going to the shop to collect';
      opts.info.innerHTML = '<b>' + stage + '</b>' + (r.name ? ' · ' + esc(r.name) : '') +
        (r.km_left != null ? ' · about ' + (Math.round(r.km_left * 10) / 10) + ' km away' : '') +
        (r.seen_s != null ? '<br><small>Location updated ' + ago(r.seen_s) + '</small>' : '<br><small>The rider\'s location will show here when their phone shares it.</small>') +
        (r.phone ? '<br><a href="tel:' + esc(String(r.phone).replace(/[^\d+]/g, '')) + '">Call the rider</a>' : '');
    }
    function poll() {
      return fetch('/api/shop/orders/' + encodeURIComponent(opts.orderId) + '/tracking', { headers: { Authorization: 'Bearer ' + opts.token } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (t) {
          if (!t) return;
          words(t); draw(t);
          if (!t.live && timer) { clearInterval(timer); timer = null; }
        }).catch(function () {});
    }
    loadLeaflet().then(function (L) {
      opts.el.style.display = 'block';
      map = baseMap(L, opts.el);
      poll();
      timer = setInterval(function () { if (!document.hidden) poll(); }, opts.every || 15000);
    }).catch(function () { opts.el.style.display = 'none'; poll(); });
    return { stop: function () { if (timer) clearInterval(timer); } };
  }

  /* Plain map of many points: [{lat, lng, color, label, text}] */
  function points(el, list) {
    return loadLeaflet().then(function (L) {
      el.style.display = 'block';
      if (el._sazMap) { el._sazMap.remove(); }
      var map = baseMap(L, el); el._sazMap = map;
      var pts = [];
      list.forEach(function (p) {
        if (p.lat == null || p.lng == null) return;
        var m = p.small ? L.circleMarker([p.lat, p.lng], { radius: 6, color: p.color || '#0e7c74', fillOpacity: 0.55, weight: 1 })
          : L.marker([p.lat, p.lng], { icon: dot(L, p.color || '#0e7c74', p.label || '') });
        m.addTo(map); if (p.text) m.bindPopup(p.text);
        pts.push([p.lat, p.lng]);
      });
      if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 15 });
      return map;
    }).catch(function () {
      el.style.display = 'block';
      el.innerHTML = '<p style="padding:20px;text-align:center;color:#64748b">The map couldn\'t load. Check the internet connection and refresh.</p>';
    });
  }

  global.SAZMap = { load: loadLeaflet, track: track, points: points };
})(window);
