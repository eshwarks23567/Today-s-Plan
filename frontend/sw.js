// Today's Plan is a live-data app — caching showtimes/prices would show stale
// data offline. This worker exists only to satisfy PWA installability; every
// request still goes straight to the network.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {}); // no-op: default (network) handling applies
// a "bookings are open" notification brings the app back to the front
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" })
    .then(ws => ws.length ? ws[0].focus() : self.clients.openWindow("/")));
});
