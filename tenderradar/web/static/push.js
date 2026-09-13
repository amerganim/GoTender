// Subscribe/unsubscribe to Web Push from the profile page.
(function () {
  var btn = document.getElementById('push-btn');
  var status = document.getElementById('push-status');
  if (!btn) return;

  var supported = ('serviceWorker' in navigator) && ('PushManager' in window);
  if (!supported) {
    // iOS Safari only supports this for installed web apps, so say so plainly
    // rather than showing a button that cannot work.
    btn.style.display = 'none';
    status.textContent = 'This browser does not support push notifications. ' +
      'On iPhone, add the site to your Home Screen first.';
    return;
  }

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - base64String.length % 4) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(base64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
    return out;
  }

  function setState(subscribed) {
    btn.textContent = subscribed ? 'Turn off notifications' : 'Turn on notifications';
    btn.dataset.subscribed = subscribed ? '1' : '';
    status.textContent = subscribed
      ? 'On — we will alert you when a matching tender is about to close.'
      : 'Off — you still get the daily email.';
  }

  navigator.serviceWorker.register('/sw.js').then(function (reg) {
    return reg.pushManager.getSubscription();
  }).then(function (sub) {
    setState(!!sub);
  }).catch(function (err) {
    status.textContent = 'Notifications unavailable: ' + err.message;
  });

  btn.addEventListener('click', function () {
    btn.disabled = true;
    var wasSubscribed = !!btn.dataset.subscribed;

    navigator.serviceWorker.ready.then(function (reg) {
      if (wasSubscribed) {
        return reg.pushManager.getSubscription().then(function (sub) {
          if (!sub) return null;
          var endpoint = sub.endpoint;
          return sub.unsubscribe().then(function () {
            return fetch('/push/unsubscribe', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ endpoint: endpoint })
            });
          });
        }).then(function () { setState(false); });
      }

      return Notification.requestPermission().then(function (permission) {
        if (permission !== 'granted') {
          status.textContent = permission === 'denied'
            ? 'Blocked in browser settings. Allow notifications for this site to turn them on.'
            : 'Permission was not granted.';
          return;
        }
        return reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(window.VAPID_PUBLIC_KEY)
        }).then(function (sub) {
          return fetch('/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub.toJSON())
          });
        }).then(function () { setState(true); });
      });
    }).catch(function (err) {
      status.textContent = 'Could not change notifications: ' + err.message;
    }).finally(function () { btn.disabled = false; });
  });
})();
