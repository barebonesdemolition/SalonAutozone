/* SalonAutoZone alerts: shows a pop-up when the site sends one, even if the site is closed. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { title: 'SalonAutoZone', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'SalonAutoZone', {
    body: d.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-64.png',
    data: { url: d.url || '/alerts' },
    tag: d.tag || undefined
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/alerts';
  if (url.charAt(0) !== '/') url = '/alerts';  // only ever open our own pages
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if ('focus' in list[i]) { list[i].navigate(url); return list[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
