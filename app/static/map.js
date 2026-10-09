// Event map (DC-06): OpenStreetMap tiles, no geolocation.
(function () {
  "use strict";
  const el = document.getElementById("map");
  const data = document.getElementById("markers");
  if (!el || !data || typeof L === "undefined") return;
  const markers = JSON.parse(data.textContent);
  const map = L.map(el, { scrollWheelZoom: false }).setView([51.16, 10.45], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bounds = [];
  markers.forEach((m) => {
    const popup = `<div class="map-popup">
      ${m.thumb ? `<img src="${esc(m.thumb)}" alt="">` : ""}
      <strong${m.cancelled ? ' style="text-decoration:line-through"' : ""}>${esc(m.title)}</strong><br>
      ${m.cancelled ? "<strong style='color:#c0262d'>ABGESAGT</strong><br>" : ""}
      ${esc(m.when)} · ${esc(m.where)}<br>
      <a href="/events/${m.id}">Details</a></div>`;
    L.marker([m.lat, m.lng], { title: m.title }).addTo(map).bindPopup(popup);
    bounds.push([m.lat, m.lng]);
  });
  if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
})();
