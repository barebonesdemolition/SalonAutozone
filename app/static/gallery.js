/* Photo gallery for part and car pages.
   SAZGallery.mount(el, { type: 'part'|'vehicle', id, cover, title, ownerId, icon }) */
(function (global) {
  'use strict';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function safeUrl(u) { u = String(u || '').trim(); return (/^https?:\/\//i.test(u) || /^\/(?!\/)/.test(u)) ? u : ''; }
  function token() { try { return localStorage.getItem('token') || ''; } catch (e) { return ''; } }
  function me() { try { return JSON.parse(localStorage.getItem('userInfo') || '{}') || {}; } catch (e) { return {}; } }

  function mount(el, o) {
    var photos = o.cover ? [{ id: null, url: o.cover }] : [];
    var isOwner = !!token() && me().id != null && String(me().id) === String(o.ownerId);
    var base = '/api/photos/' + o.type + '/' + encodeURIComponent(o.id);

    function render() {
      var list = photos.filter(function (p) { return safeUrl(p.url); });
      var h = '<div class="gal">';
      if (!list.length) {
        h += '<div class="gallery"><svg aria-hidden="true"><use href="#' + o.icon + '"/></svg></div>';
      } else {
        h += '<div class="gal-main" tabindex="0" aria-label="Photos">' + list.map(function (p, i) {
          return '<img src="' + esc(safeUrl(p.url)) + '" alt="' + esc(o.title) + ', photo ' + (i + 1) + '"' + (i ? ' loading="lazy"' : '') + '>';
        }).join('') + '</div>';
        if (list.length > 1) {
          h += '<span class="gal-count" aria-hidden="true">1 / ' + list.length + '</span>';
          h += '<div class="gal-thumbs">' + list.map(function (p, i) {
            return '<button type="button" data-i="' + i + '" style="background-image:url(\'' + esc(safeUrl(p.url)) + '\')" aria-label="Photo ' + (i + 1) + '"' + (i === 0 ? ' aria-current="true"' : '') + '></button>';
          }).join('') + '</div>';
        }
      }
      if (isOwner) {
        h += '<div class="gal-owner"><label class="btn btn-ghost" style="cursor:pointer">Add photos<input type="file" accept="image/jpeg,image/png,image/webp" multiple class="sr-only" data-add></label>' +
          (list.length > 1 ? '<button type="button" class="btn btn-ghost" data-cover>Make this the cover</button>' : '') +
          (list.length ? '<button type="button" class="btn btn-ghost" data-del>Delete this photo</button>' : '') +
          '<span class="hint" data-msg style="margin:0"></span></div>';
      }
      el.innerHTML = h + '</div>';
      wire(list);
    }

    function current() {
      var main = el.querySelector('.gal-main');
      return main ? Math.round(main.scrollLeft / Math.max(1, main.clientWidth)) : 0;
    }

    function wire(list) {
      var main = el.querySelector('.gal-main');
      var count = el.querySelector('.gal-count');
      if (main) main.addEventListener('scroll', function () {
        var i = current();
        if (count) count.textContent = (i + 1) + ' / ' + list.length;
        el.querySelectorAll('.gal-thumbs button').forEach(function (b) { b.setAttribute('aria-current', String(Number(b.dataset.i) === i)); });
      }, { passive: true });
      el.querySelectorAll('.gal-thumbs button').forEach(function (b) {
        b.addEventListener('click', function () { main.scrollTo({ left: Number(b.dataset.i) * main.clientWidth, behavior: 'smooth' }); });
      });
      if (!isOwner) return;
      var msg = el.querySelector('[data-msg]');
      function say(t) { if (msg) msg.textContent = t; }
      var add = el.querySelector('[data-add]');
      if (add) add.addEventListener('change', function () {
        var files = Array.from(this.files || []), i = 0, failed = 0;
        function next() {
          if (i >= files.length) { if (failed) say(failed + ' photo(s) didn\'t upload.'); return load(); }
          say('Uploading ' + (i + 1) + ' of ' + files.length + '…');
          var fd = new FormData(); fd.append('file', files[i]);
          return fetch(base, { method: 'POST', headers: { Authorization: 'Bearer ' + token() }, body: fd })
            .then(function (r) { if (!r.ok) { failed++; return r.json().then(function (d) { say(d.detail || 'Upload failed'); }).catch(function () {}); } })
            .catch(function () { failed++; })
            .then(function () { i++; return next(); });
        }
        next();
      });
      var cov = el.querySelector('[data-cover]'), del = el.querySelector('[data-del]');
      function act(method, suffix) {
        var p = list[current()];
        if (!p || p.id == null) { say('This photo can\'t be changed. Add new photos instead.'); return; }
        fetch(base + suffix(p.id), { method: method, headers: { Authorization: 'Bearer ' + token() } })
          .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || 'Something went wrong'); }); })
          .then(load).catch(function (e) { say(e.message); });
      }
      if (cov) cov.addEventListener('click', function () { act('POST', function (id) { return '/cover/' + id; }); });
      if (del) del.addEventListener('click', function () { if (confirm('Delete this photo?')) act('DELETE', function (id) { return '/' + id; }); });
    }

    function load() {
      return fetch(base).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (d && d.photos) photos = d.photos;
        render();
      }).catch(render);
    }
    render();
    load();
  }
  global.SAZGallery = { mount: mount };
})(window);
