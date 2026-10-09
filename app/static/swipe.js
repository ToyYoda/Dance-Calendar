// Mobile swipe review (DC-32): right = approve, left = reject, undo the last action.
(function () {
  "use strict";
  const stage = document.getElementById("swipe-stage");
  const empty = document.getElementById("swipe-empty");
  const msg = document.getElementById("swipe-msg");
  const undoBtn = document.getElementById("swipe-undo");
  const zoom = document.getElementById("zoom");
  const zoomImg = document.getElementById("zoom-img");
  const THRESHOLD = 110;
  let queue = [];
  let lastDecision = null;
  let busy = false;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (text) => { msg.textContent = text; };

  async function load() {
    const res = await fetch("/admin/api/queue");
    if (res.status === 401) { location.href = "/admin/login?next=/admin/swipe"; return; }
    queue = (await res.json()).items;
    render();
  }

  function render() {
    stage.querySelectorAll(".swipe-card").forEach((c) => c.remove());
    empty.hidden = queue.length > 0;
    // draw the next card underneath for a smooth transition
    queue.slice(0, 2).reverse().forEach((item, i, arr) => stage.appendChild(card(item, i === arr.length - 1)));
  }

  function card(item, interactive) {
    const el = document.createElement("article");
    el.className = "swipe-card";
    el.dataset.id = item.id;
    el.innerHTML = `
      <span class="stamp stamp-ok">FREIGEBEN</span><span class="stamp stamp-no">ABLEHNEN</span>
      ${item.preview ? `<img src="${esc(item.preview)}" alt="Flyer" draggable="false" data-original="${esc(item.original)}">` : `<div class="thumb-empty big">Kein Flyer</div>`}
      <div class="swipe-info">
        <strong>${esc(item.title)}</strong>
        <span class="small">${esc(item.when)} · ${esc(item.where)}</span>
        <span class="small">${item.styles.map(esc).join(", ")}${item.confidence != null ? ` · Score ${Math.round(item.confidence * 100)} %` : ""}</span>
        ${item.missing.length ? `<span class="small warn-text">Fehlt: ${item.missing.map(esc).join(", ")}</span>` : ""}
        ${item.low.length ? `<span class="small warn-text">Unsicher: ${item.low.map(esc).join(", ")}</span>` : ""}
        ${item.locked_by ? `<span class="small">Gesperrt: ${esc(item.locked_by)} bearbeitet gerade</span>` : ""}
        <a class="small" href="/admin/events/${item.id}">Bearbeiten</a>
      </div>`;
    if (interactive) attachDrag(el);
    return el;
  }

  function attachDrag(el) {
    let startX = 0, startY = 0, dx = 0, dragging = false, moved = false;
    const ok = el.querySelector(".stamp-ok");
    const no = el.querySelector(".stamp-no");
    el.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest("a")) return;
      dragging = true; moved = false; startX = ev.clientX; startY = ev.clientY; dx = 0;
      el.classList.add("dragging");
      el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener("pointermove", (ev) => {
      if (!dragging) return;
      dx = ev.clientX - startX;
      if (Math.abs(dx) > 6 || Math.abs(ev.clientY - startY) > 6) moved = true;
      el.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
      ok.style.opacity = Math.max(0, Math.min(1, dx / THRESHOLD));
      no.style.opacity = Math.max(0, Math.min(1, -dx / THRESHOLD));
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      el.classList.remove("dragging");
      if (dx > THRESHOLD) decide("approve");
      else if (dx < -THRESHOLD) decide("reject");
      else {
        el.style.transform = "";
        ok.style.opacity = no.style.opacity = 0;
      }
    };
    el.addEventListener("pointerup", (ev) => {
      const img = ev.target.closest("img[data-original]");
      end();
      if (!moved && img) openZoom(img.dataset.original);
    });
    el.addEventListener("pointercancel", end);
  }

  async function decide(action) {
    if (busy || !queue.length) return;
    busy = true;
    const item = queue[0];
    const el = stage.querySelector(`.swipe-card[data-id="${item.id}"]`);
    const res = await fetch(`/admin/api/events/${item.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const data = await res.json().catch(() => ({}));
    busy = false;
    if (!res.ok) {
      say(data.error || "Aktion fehlgeschlagen.");
      if (el) el.style.transform = "";
      if (res.status === 409) { await load(); }
      return;
    }
    if (el) {
      el.style.transform = `translateX(${action === "approve" ? 600 : -600}px) rotate(${action === "approve" ? 25 : -25}deg)`;
      el.style.opacity = "0";
    }
    lastDecision = { id: data.decision_id, title: item.title };
    undoBtn.disabled = false;
    say(`${action === "approve" ? "Freigegeben" : "Abgelehnt"}: ${item.title}`);
    queue.shift();
    setTimeout(render, 220);
  }

  async function undo() {
    if (!lastDecision || busy) return;
    busy = true;
    const res = await fetch("/admin/api/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision_id: lastDecision.id }),
    });
    busy = false;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { say(data.error || "Rückgängig fehlgeschlagen."); return; }
    say(`Zurückgenommen: ${lastDecision.title}`);
    lastDecision = null;
    undoBtn.disabled = true;
    await load();
  }

  function openZoom(src) {
    zoomImg.src = src;
    zoom.hidden = false;
  }
  document.getElementById("zoom-close").addEventListener("click", () => { zoom.hidden = true; });
  document.getElementById("swipe-approve").addEventListener("click", () => decide("approve"));
  document.getElementById("swipe-reject").addEventListener("click", () => decide("reject"));
  undoBtn.addEventListener("click", undo);
  document.addEventListener("keydown", (ev) => {
    if (!zoom.hidden && ev.key === "Escape") zoom.hidden = true;
    else if (ev.key === "ArrowRight") decide("approve");
    else if (ev.key === "ArrowLeft") decide("reject");
    else if (ev.key.toLowerCase() === "z" && (ev.ctrlKey || ev.metaKey)) undo();
  });
  load();
})();
