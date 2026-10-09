// Progressive enhancement: every page works without JavaScript.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // --- Theme toggle -----------------------------------------------------------
  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const root = document.documentElement;
      // Dark is the default look; light is the optional "candle-light" variant
      const next = root.dataset.theme === "light" ? "dark" : "light";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
    });
  }

  // --- Confirmations ------------------------------------------------------------
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    const btn = ev.submitter;
    const msg = (btn && btn.dataset.confirm) || form.dataset.confirm;
    if (msg && !confirm(msg)) ev.preventDefault();
  }, true);

  // --- Copy links -----------------------------------------------------------------
  document.addEventListener("click", async (ev) => {
    const el = ev.target.closest("[data-copy]");
    if (!el) return;
    ev.preventDefault();
    try {
      await navigator.clipboard.writeText(el.dataset.copy);
      const old = el.textContent;
      el.textContent = "Kopiert";
      setTimeout(() => { el.textContent = old; }, 1500);
    } catch (e) {
      prompt("Link kopieren:", el.dataset.copy);
    }
  });

  // --- "Bin dabei" with near-realtime counter (DC-05) ------------------------------
  const counter = $("[data-count-for]");
  function renderCount(count) {
    if (counter) counter.innerHTML = `<strong>${count}</strong> ${count === 1 ? "Zusage" : "Zusagen"}`;
  }
  function renderAttend(form, attending) {
    form.querySelector("input[name=attend]").value = attending ? "0" : "1";
    const btn = form.querySelector(".attend-btn");
    btn.textContent = attending ? "Du bist dabei" : "Bin dabei";
    btn.classList.toggle("btn-primary", !attending);
    btn.classList.toggle("btn-secondary", attending);
    btn.setAttribute("aria-pressed", String(attending));
  }
  const attendForm = $("form.attend");
  if (attendForm) {
    attendForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const want = attendForm.querySelector("input[name=attend]").value === "1";
      const res = await fetch(`/api/events/${attendForm.dataset.event}/attendance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attend: want }),
      });
      if (res.ok) {
        const data = await res.json();
        renderAttend(attendForm, data.attending);
        renderCount(data.count);
      } else if (res.status === 401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname);
      }
    });
  }
  if (counter) {
    const id = counter.dataset.countFor;
    setInterval(async () => {
      if (document.hidden) return;
      try {
        const res = await fetch(`/api/events/${id}/attendance`);
        if (res.ok) renderCount((await res.json()).count);
      } catch (e) { /* offline */ }
    }, 15000);
  }

  // --- Live form validation (DC-20) -----------------------------------------------------
  function fieldOf(input) { return input.closest(".field"); }
  function setError(field, message) {
    if (!field) return;
    let p = field.querySelector(".field-error.live");
    field.classList.toggle("has-error", Boolean(message));
    if (message) {
      if (!p) {
        p = document.createElement("p");
        p.className = "field-error live";
        p.setAttribute("role", "alert");
        field.appendChild(p);
      }
      p.textContent = message;
    } else if (p) {
      p.remove();
    }
  }
  function validateInput(input) {
    let msg = "";
    if (input.hasAttribute("data-required") && !input.value.trim()) msg = "Pflichtfeld.";
    else if (input.type === "url" && input.value && !/^https?:\/\//.test(input.value)) msg = "Der Link muss mit https:// beginnen.";
    else if (input.name === "end" && input.value) {
      const start = input.form.querySelector("[name=start]").value;
      if (start && input.value <= start) msg = "Das Ende muss nach dem Beginn liegen.";
    }
    setError(fieldOf(input), msg);
    return !msg;
  }
  function validateGroup(group) {
    const ok = $$("input[type=checkbox]", group).some((c) => c.checked);
    setError(group, ok ? "" : "Bitte mindestens einen Tanzstil wählen.");
    return ok;
  }
  $$("form.event-form").forEach((form) => {
    const inputs = $$("input[data-required], input[type=url], input[name=end]", form);
    inputs.forEach((input) => {
      input.addEventListener("blur", () => validateInput(input));
      input.addEventListener("input", () => { if (fieldOf(input)?.classList.contains("has-error")) validateInput(input); });
    });
    $$("[data-required-group]", form).forEach((group) => group.addEventListener("change", () => validateGroup(group)));
    form.addEventListener("submit", (ev) => {
      form.dataset.submitting = "1";
      if (ev.submitter && ev.submitter.hasAttribute("data-skip-validation")) return;
      const okInputs = inputs.map(validateInput).every(Boolean);
      const okGroups = $$("[data-required-group]", form).map(validateGroup).every(Boolean);
      if (!okInputs || !okGroups) {
        ev.preventDefault();
        delete form.dataset.submitting;
        form.querySelector(".has-error")?.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  });

  // --- Release the edit lock when leaving without saving (DC-34) -----------------------
  const lockForm = $("form[data-unlock]");
  if (lockForm) {
    window.addEventListener("pagehide", () => {
      if (!lockForm.dataset.submitting) navigator.sendBeacon(lockForm.dataset.unlock);
    });
  }

  // --- Review shortcuts (DC-31) ------------------------------------------------------------
  const review = $("form[data-review]");
  if (review) {
    document.addEventListener("keydown", (ev) => {
      if (ev.target.closest("input, textarea, select") || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const key = ev.key.toLowerCase();
      const click = (sel) => { const el = $(sel); if (el && !el.disabled) { ev.preventDefault(); el.click(); } };
      if (key === "a") click("button[value=approve]");
      else if (key === "r") click("button[value=reject]");
      else if (key === "d") click("button[value=delete]");
      else if (key === "n") { const next = $("a[data-next]"); if (next) { ev.preventDefault(); location.href = next.href; } }
    });
  }

  // --- Gallery batch actions (DC-33) ----------------------------------------------------------
  const batch = $("#batch-form");
  if (batch) {
    const checks = $$(".gallery-check", batch);
    const countEl = $("#selected-count");
    const update = () => { countEl.textContent = checks.filter((c) => c.checked).length; };
    checks.forEach((c) => c.addEventListener("change", update));
    $("#select-all").addEventListener("change", (ev) => { checks.forEach((c) => { c.checked = ev.target.checked; }); update(); });
    const action = $("#batch-action");
    action.addEventListener("change", () => { $("#location-fields").hidden = action.value !== "set_location"; });
    $$(".gallery-open", batch).forEach((a) => a.addEventListener("click", (ev) => ev.stopPropagation()));
    batch.addEventListener("submit", (ev) => {
      const n = checks.filter((c) => c.checked).length;
      if (!n) { ev.preventDefault(); alert("Bitte mindestens ein Event auswählen."); return; }
      const label = action.options[action.selectedIndex].text;
      if (!confirm(`„${label}“ für ${n} ${n === 1 ? "Event" : "Events"} ausführen?`)) ev.preventDefault();
    });
  }
})();
