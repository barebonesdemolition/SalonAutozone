/* SalonAutoZone shared page chrome: header, phone tab bar and footer.
   Usage: <link rel="stylesheet" href="/static/ui.css"> and
          <div id="site-header"></div> ... <script src="/static/site.js" defer></script>
   Add data-footer="off" on <body> to skip the footer, data-tabbar="off" to skip the tab bar. */
(function () {
  'use strict';

  var ICONS = {
    part: '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M4.9 4.9 7 7m10 10 2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/>',
    car: '<path d="M5 17h14v-5l-2-5H7l-2 5v5z"/><path d="M5 12h14"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/>',
    vin: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9v6m3-6v6m3-6v6m4-6v6"/>',
    cart: '<path d="M3 4h2l2.4 11h10.2L20 8H6.2"/><circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    garage: '<path d="M3 21V9l9-6 9 6v12"/><path d="M7 21v-7h10v7"/>'
  };
  function icon(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>';
  }
  function token() {
    try { return localStorage.getItem('token') || localStorage.getItem('access_token') || ''; } catch (e) { return ''; }
  }
  function cartCount() {
    try {
      return (JSON.parse(localStorage.getItem('saz_cart') || '[]') || []).reduce(function (s, i) { return s + (Number(i.qty) || 0); }, 0);
    } catch (e) { return 0; }
  }

  var path = location.pathname;
  var params = new URLSearchParams(location.search);
  var onHome = path === '/' || path === '/marketplace';
  var section = onHome ? (params.get('tab') === 'cars' ? 'cars' : 'parts')
    : /^\/vehicle\//.test(path) ? 'cars'
    : /^\/(part|catalog)\//.test(path) ? 'parts'
    : /^\/vin/.test(path) ? 'vin'
    : /^\/(garage)/.test(path) ? 'garage'
    : /^\/(cart|checkout)/.test(path) ? 'cart'
    : /^\/(my-account|login|dashboard|my-subscription)/.test(path) ? 'account'
    : /^\/(sell|become-a-seller)/.test(path) ? 'sell' : '';
  function cur(name) { return section === name ? ' aria-current="page"' : ''; }

  var loggedIn = !!token();
  var accountHref = loggedIn ? '/my-account' : '/login';
  var count = cartCount();

  function ensureFont() {
    if (document.querySelector('link[data-saz-font]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = 'https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&display=swap';
    l.setAttribute('data-saz-font', '');
    document.head.appendChild(l);
  }

  function header() {
    return '<header class="saz-header"><div class="wrap">' +
      '<a class="saz-logo" href="/"><img src="/static/logo.png" alt="" width="40" height="40"><b>Salon<span>AutoZone</span></b></a>' +
      '<nav class="saz-nav" aria-label="Main">' +
        '<a href="/?tab=parts"' + cur('parts') + '>Parts</a>' +
        '<a href="/?tab=cars"' + cur('cars') + '>Cars</a>' +
        '<a href="/vin-tool"' + cur('vin') + '>VIN check</a>' +
        '<a href="/garage"' + cur('garage') + '>My garage</a>' +
      '</nav>' +
      '<div class="saz-actions">' +
        '<a class="btn btn-primary btn-sell" href="/sell" data-saz-sell>' + icon('plus') + 'Sell</a>' +
        '<a class="icon-btn" href="/cart" aria-label="Cart' + (count ? ', ' + count + ' items' : '') + '">' + icon('cart') +
          '<span class="badge-count" id="saz-cart-count"' + (count ? '' : ' hidden') + '>' + count + '</span></a>' +
        '<a class="icon-btn" href="' + accountHref + '" aria-label="' + (loggedIn ? 'My account' : 'Sign in') + '">' + icon('user') + '</a>' +
      '</div>' +
    '</div></header>';
  }

  function tabbar() {
    return '<nav class="saz-tabbar" aria-label="Quick links">' +
      '<a href="/?tab=parts"' + cur('parts') + '>' + icon('part') + 'Parts</a>' +
      '<a href="/?tab=cars"' + cur('cars') + '>' + icon('car') + 'Cars</a>' +
      '<a class="sell" href="/sell" data-saz-sell' + cur('sell') + '><span class="plus">' + icon('plus') + '</span>Sell</a>' +
      '<a href="/vin-tool"' + cur('vin') + '>' + icon('vin') + 'VIN</a>' +
      '<a href="' + accountHref + '"' + cur('account') + '>' + icon('user') + (loggedIn ? 'Account' : 'Sign in') + '</a>' +
    '</nav>';
  }

  function footer() {
    return '<footer class="saz-footer"><div class="wrap">' +
      '<div><h4>SalonAutoZone</h4><p>Car parts and cars from sellers across Freetown, Bo, Kenema, Makeni and beyond.</p></div>' +
      '<div><h4>Buy</h4><a href="/?tab=parts">Parts</a><a href="/?tab=cars">Cars</a><a href="/businesses">Verified sellers</a><a href="/vin-tool">VIN check</a></div>' +
      '<div><h4>Sell</h4><a href="/sell">List a part or car</a><a href="/become-a-seller">Register a business</a><a href="/plans">Seller plans</a></div>' +
      '<div><h4>Account</h4><a href="' + accountHref + '">' + (loggedIn ? 'My account' : 'Sign in') + '</a><a href="/garage">My garage</a><a href="/drive">Deliver with us</a></div>' +
    '</div></footer>';
  }

  function mount() {
    ensureFont();
    var body = document.body;
    var slot = document.getElementById('site-header');
    if (slot) slot.outerHTML = header();
    else body.insertAdjacentHTML('afterbegin', header());
    if (body.getAttribute('data-footer') !== 'off') {
      body.insertAdjacentHTML('beforeend', footer());
    }
    if (body.getAttribute('data-tabbar') !== 'off') {
      body.insertAdjacentHTML('beforeend', tabbar());
      body.classList.add('has-tabbar');
    }
  }

  // "Sell" asks what you're selling: parts are listed from the home page, cars on /sell.
  function sellSheet() {
    var el = document.getElementById('saz-sell-sheet');
    if (!el) {
      document.body.insertAdjacentHTML('beforeend',
        '<div class="overlay" id="saz-sell-sheet" role="dialog" aria-modal="true" aria-labelledby="saz-sell-title"><div class="modal" style="max-width:440px">' +
        '<button class="x" type="button" aria-label="Close" data-saz-close>&times;</button>' +
        '<h2 id="saz-sell-title">What are you selling?</h2>' +
        '<div style="display:grid;gap:10px;margin-top:8px">' +
        '<a class="btn btn-ghost btn-lg btn-block" style="justify-content:flex-start" href="/?action=list-part">' + icon('part') + 'A part or accessory</a>' +
        '<a class="btn btn-ghost btn-lg btn-block" style="justify-content:flex-start" href="/sell">' + icon('car') + 'A car, truck or bike</a>' +
        '</div></div></div>');
      el = document.getElementById('saz-sell-sheet');
      el.addEventListener('click', function (e) {
        if (e.target === el || e.target.closest('[data-saz-close]')) el.classList.remove('open');
      });
    }
    el.classList.add('open');
  }
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-saz-sell]');
    if (t) { e.preventDefault(); sellSheet(); }
  });
  document.addEventListener('keydown', function (e) {
    var el = document.getElementById('saz-sell-sheet');
    if (e.key === 'Escape' && el) el.classList.remove('open');
  });

  document.addEventListener('saz-cart-change', function () {
    var el = document.getElementById('saz-cart-count');
    if (!el) return;
    var n = cartCount();
    el.textContent = n;
    el.hidden = !n;
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
