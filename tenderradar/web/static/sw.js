// Service worker for Web Push.
// Served from the site root so its scope covers every page.

self.addEventListener('push', function (event) {
  if (!event.data) return;
  var payload;
  try { payload = event.data.json(); } catch (e) { return; }

  event.waitUntil(
    self.registration.showNotification(payload.title || 'TenderRadar', {
      body: payload.body || '',
      // Same tag replaces an earlier notification instead of stacking, so a
      // phone never shows five near-identical alerts.
      tag: payload.tag || 'tenderradar',
      renotify: true,
      data: { url: payload.url || '/' },
      requireInteraction: false
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/';
  // Focus an already-open tab rather than piling up new ones.
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf(url) !== -1 && 'focus' in list[i]) return list[i].focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
