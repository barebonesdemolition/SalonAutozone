/* "Ask the seller" box for part and car pages. Saves the enquiry (so it shows in
   the seller's Enquiries tab) and offers WhatsApp for a faster reply.
   SAZEnquiry.mount(el, { type: 'part'|'vehicle', id, title, whatsapp }) */
(function (global) {
  'use strict';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function me() { try { return JSON.parse(localStorage.getItem('userInfo') || '{}') || {}; } catch (e) { return {}; } }

  function mount(el, o) {
    var u = me();
    var suggestion = o.type === 'part' ? 'Hi, is this still available? Does it fit my car?' : 'Hi, is this car still available? When can I see it?';
    el.innerHTML = '<section class="box"><h2>Ask the seller</h2>' +
      '<form novalidate data-enq>' +
      '<label class="field" style="margin-top:6px"><span>Your message</span><textarea class="input" name="m" rows="3" maxlength="1000">' + esc(suggestion) + '</textarea></label>' +
      '<div style="display:grid;gap:0 10px;grid-template-columns:1fr 1fr">' +
      '<label class="field"><span>Your name</span><input class="input" name="n" maxlength="80" autocomplete="name" value="' + esc(u.full_name || '') + '"></label>' +
      '<label class="field"><span>Your phone</span><input class="input" name="p" type="tel" maxlength="25" autocomplete="tel" value="' + esc(u.phone || '') + '"></label>' +
      '</div>' +
      '<button class="btn btn-primary btn-block" style="margin-top:12px" type="submit">Send to the seller</button>' +
      '<p class="hint" data-msg aria-live="polite" style="margin-top:8px">The seller sees your number and can call or WhatsApp you.</p>' +
      '</form></section>';
    var form = el.querySelector('[data-enq]'), msg = el.querySelector('[data-msg]');
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = { listing_type: o.type, listing_id: Number(o.id), buyer_name: form.n.value.trim(), buyer_phone: form.p.value.trim(), buyer_message: form.m.value.trim() };
      if (body.buyer_name.length < 2) { msg.textContent = 'Please add your name.'; msg.style.color = 'var(--bad)'; return; }
      if (body.buyer_phone.replace(/\D/g, '').length < 6) { msg.textContent = 'Please add a phone number the seller can reach.'; msg.style.color = 'var(--bad)'; return; }
      var btn = form.querySelector('button'); btn.disabled = true; btn.textContent = 'Sending…';
      fetch('/api/inquiries/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function (r) { if (!r.ok) throw new Error(); })
        .then(function () {
          form.innerHTML = '<p class="fit-yes" style="margin:6px 0">Sent. The seller will call or message you on ' + esc(body.buyer_phone) + '.</p>' +
            (o.whatsapp ? '<a class="btn btn-wa btn-block" href="' + esc(o.whatsapp) + '" target="_blank" rel="noopener">Want a faster reply? WhatsApp them</a>' : '');
        })
        .catch(function () { btn.disabled = false; btn.textContent = 'Send to the seller'; msg.textContent = 'That didn\'t send. Check your connection and try again.'; msg.style.color = 'var(--bad)'; });
    });
  }
  global.SAZEnquiry = { mount: mount };
})(window);
