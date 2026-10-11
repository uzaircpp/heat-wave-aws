/* Rahat service worker: shows push alerts (rest now, safe again, heavy rain, official warnings)
   even when the app is closed, and opens the right screen when one is tapped. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) { m = { title: 'Raहत', body: e.data ? e.data.text() : '' }; }
  var actions = [{ action: 'open', title: 'Open' }];
  if (m.place) actions.unshift({ action: 'directions', title: 'Directions' });
  var urgent = m.kind === 'rest' || m.kind === 'rain' || m.kind === 'official' || m.kind === 'broadcast';
  e.waitUntil(self.registration.showNotification(m.title || 'Raहत', {
    body: m.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/badge-72.png',
    tag: m.tag || m.kind || 'rahat',
    renotify: true,
    requireInteraction: urgent,
    vibrate: urgent ? [300, 120, 300, 120, 300] : [200],
    actions: actions,
    data: { url: m.url || '/', place: m.place || null }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var d = e.notification.data || {};
  if (e.action === 'directions' && d.place) {
    e.waitUntil(self.clients.openWindow('https://www.google.com/maps/dir/?api=1&travelmode=walking&destination=' + d.place.lat + ',' + d.place.lon));
    return;
  }
  var target = new URL(d.url || '/', self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url.indexOf(self.registration.scope) === 0 && 'focus' in list[i]) {
        return list[i].navigate(target).then(function (c) { return (c || list[i]).focus(); }).catch(function () { return list[i].focus(); });
      }
    }
    return self.clients.openWindow(target);
  }));
});
