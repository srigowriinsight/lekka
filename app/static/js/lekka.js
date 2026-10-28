// Lekka: popups (dialog), forms submitted from popups, grid expand/collapse, split helper.
(function () {
  const pop = () => document.getElementById("pop");
  const FETCH = { "X-Requested-With": "fetch" };

  async function openDialog(url) {
    const r = await fetch(url, { headers: FETCH, credentials: "same-origin" });
    const html = await r.text();
    const d = pop();
    d.innerHTML = html;
    if (!d.open) d.showModal();
    init(d);
    const f = d.querySelector("input:not([type=hidden]):not([type=radio]):not([type=checkbox]), select, textarea");
    if (f) f.focus();
  }
  window.lekkaOpen = openDialog;

  let clickTimer = null;
  document.addEventListener("click", (ev) => {
    if (ev.target.closest(".quick-edit")) return;             // typing in an inline amount box
    const el = ev.target.closest("[data-dialog]");
    if (el && el.dataset.edit) {
      // A figure that can be double-clicked: wait a moment so a double-click edits instead of opening the popup.
      ev.preventDefault();
      clearTimeout(clickTimer);
      if (ev.detail > 1) return;
      clickTimer = setTimeout(() => openDialog(el.dataset.dialog), 260);
      return;
    }
    if (el) { ev.preventDefault(); openDialog(el.dataset.dialog || el.getAttribute("href")); return; }
    if (ev.target.closest("[data-close]")) { pop().close(); return; }
    const tg = ev.target.closest("[data-toggle]");
    if (tg) { toggleHead(tg.dataset.toggle); return; }
    const conf = ev.target.closest("[data-confirm]");
    if (conf && !confirm(conf.dataset.confirm)) { ev.preventDefault(); }
  });

  // New item dated before today: it is money that already moved, so tick "already paid / received"
  // (Lekka then offers the matching plan instead of creating a duplicate). It can still be unticked.
  document.addEventListener("change", (ev) => {
    const d = ev.target;
    if (!d.matches('input[name="planned_date"][data-today]')) return;
    const box = d.form && d.form.querySelector('input[name="already_settled"]');
    if (box && d.value && d.value < d.dataset.today) box.checked = true;
  });

  document.addEventListener("dblclick", (ev) => {
    const ed = ev.target.closest("[data-edit]");
    if (ed) { ev.preventDefault(); clearTimeout(clickTimer); quickEdit(ed); return; }
    const el = ev.target.closest("[data-new]");
    if (el) { ev.preventDefault(); openDialog(el.dataset.new); }
  });

  // ------------------------------------------------ grid: double-click a figure to change its amount
  function quickEdit(td) {
    if (td.querySelector(".quick-edit")) return;
    const before = td.innerHTML;
    const input = document.createElement("input");
    input.className = "quick-edit";
    input.inputMode = "decimal";
    input.value = td.dataset.value;
    input.setAttribute("aria-label", "New amount");
    input.style.width = Math.max(70, td.clientWidth - 18) + "px";   // keep the column its usual width
    td.innerHTML = "";
    td.appendChild(input);
    input.focus();
    input.select();
    let busy = false;
    const restore = () => { td.innerHTML = before; };
    const save = async () => {
      if (busy) return;
      const v = input.value.trim();
      if (v === "" || v.replace(/[,₹\s]/g, "") === String(td.dataset.value)) { restore(); return; }
      busy = true;
      const body = new FormData();
      body.append("amount", v);
      body.append("version", td.dataset.version);
      body.append("next", window.location.pathname + window.location.search);
      const tok = document.querySelector('meta[name="csrf-token"]');
      if (tok) body.append("csrf_token", tok.content);
      try {
        const r = await fetch(td.dataset.edit, { method: "POST", body, headers: FETCH, credentials: "same-origin" });
        const data = await r.json();
        if (data.redirect) { window.location = data.redirect; return; }
        input.classList.add("bad");
        input.title = data.error || "Could not save.";
        showTip(td, input.title);
      } catch (e) {
        showTip(td, "Could not save. Check the connection and try again.");
      }
      busy = false;
      input.focus();
    };
    input.addEventListener("keydown", (k) => {
      if (k.key === "Enter") { k.preventDefault(); save(); }
      if (k.key === "Escape") { k.preventDefault(); restore(); }
    });
    input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input && !busy) save(); }, 120));
  }
  function showTip(td, text) {
    let tip = td.querySelector(".quick-tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "quick-tip"; td.appendChild(tip); }
    tip.textContent = text;
  }

  document.addEventListener("submit", async (ev) => {
    const form = ev.target;
    if (!form.matches("form.js-form")) return;
    ev.preventDefault();
    const err = form.querySelector(".form-error");
    const btn = form.querySelector("button[type=submit], button:not([type])");
    if (btn) btn.disabled = true;           // no double saves
    try {
      const r = await fetch(form.action, { method: "POST", body: new FormData(form, ev.submitter), headers: FETCH,
                                           credentials: "same-origin" });
      const data = await r.json();
      if (data.redirect) { window.location = data.redirect; return; }
      if (data.open) { openDialog(data.open); return; }
      if (data.html) { pop().innerHTML = data.html; init(pop()); return; }
      if (data.error && err) { err.textContent = data.error; err.classList.add("on"); }
    } catch (e) {
      if (err) { err.textContent = "Could not save. Check the connection and try again."; err.classList.add("on"); }
    } finally { if (btn) btn.disabled = false; }
  });

  // ------------------------------------------------ grid: expand / collapse heads
  function toggleHead(key, force) {
    const rows = document.querySelectorAll(`tr[data-parent="${key}"]`);
    const btn = document.querySelector(`[data-toggle="${key}"]`);
    const show = force !== undefined ? force : (rows[0] && rows[0].hidden);
    rows.forEach((r) => (r.hidden = !show));
    if (btn) btn.textContent = show ? "▾" : "▸";
  }
  // "Custom": switch the range form to day columns and open the From date.
  window.lekkaCustom = function (link) {
    const from = document.getElementById("range-from");
    if (!from) return true;
    from.form.querySelector("input[name=freq]").value = "day";
    link.parentElement.querySelectorAll("a").forEach((a) => a.classList.remove("on"));
    link.classList.add("on");
    from.focus();
    try { from.showPicker(); } catch (e) { /* older browsers: the field is focused */ }
    return false;
  };

  window.lekkaExpandAll = function (show) {
    document.querySelectorAll("[data-toggle]").forEach((b) => toggleHead(b.dataset.toggle, show));
  };

  // ------------------------------------------------ list pages: select many
  document.addEventListener("change", (ev) => {
    if (ev.target.matches(".pick")) {
      const n = document.querySelectorAll(".pick:checked").length;
      const bar = document.querySelector(".bulkbar");
      if (bar) { bar.classList.toggle("on", n > 0); bar.querySelector(".n").textContent = n; }
    }
  });
  window.lekkaBulk = function (form) {
    form.querySelectorAll("input[name=ids]").forEach((i) => i.remove());
    document.querySelectorAll(".pick:checked").forEach((c) => {
      const h = document.createElement("input"); h.type = "hidden"; h.name = "ids"; h.value = c.value; form.appendChild(h);
    });
    return true;
  };

  // ------------------------------------------------ popup helpers
  function init(root) {
    root.querySelectorAll("[data-show-when]").forEach((el) => {
      const [name, value] = el.dataset.showWhen.split("=");
      const update = () => {
        const sel = root.querySelector(`[name="${name}"]:checked`) || root.querySelector(`select[name="${name}"]`);
        el.hidden = !(sel && value.split("|").includes(sel.value));
      };
      root.querySelectorAll(`[name="${name}"]`).forEach((i) => i.addEventListener("change", update));
      update();
    });
    const parts = root.querySelector(".parts");
    if (parts) initSplit(root, parts);
  }

  function initSplit(root, parts) {
    const target = () => parseFloat((root.querySelector("[name=new_total]") || {}).value || parts.dataset.target);
    const fmt = (n) => n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    const pctMode = () => (root.querySelector("[name=mode]:checked") || {}).value === "pct";
    function recalc() {
      const rows = [...parts.querySelectorAll(".part:not(.tpl):not(.hdr):not(.skip)")];
      if (pctMode()) {
        let left = target();
        const editable = rows.filter((r) => !r.classList.contains("locked"));
        editable.forEach((r, i) => {
          const pct = parseFloat(r.querySelector(".pct").value || 0);
          const amt = r.querySelector(".amt");
          if (i === editable.length - 1) amt.value = Math.round(left * 100) / 100;
          else { const v = Math.round(target() * pct) / 100; amt.value = v; left -= v; }
        });
        rows.filter((r) => r.classList.contains("locked")).forEach((r) => (left -= parseFloat(r.querySelector(".amt").value || 0)));
      }
      let sum = 0;
      rows.forEach((r) => (sum += parseFloat((r.querySelector(".amt").value || "0").replace(/,/g, "")) || 0));
      const t = target();
      const bar = root.querySelector(".alloc");
      const pct = t > 0 ? Math.min(100, (sum / t) * 100) : 0;
      bar.querySelector("i").style.width = pct + "%";
      bar.classList.toggle("over", sum > t + 0.001);
      const diff = Math.round((t - sum) * 100) / 100;
      root.querySelector(".alloc-text").textContent =
        `Allocated ₹${fmt(sum)} of ₹${fmt(t)} · ` + (diff >= 0 ? `Unallocated ₹${fmt(diff)}` : `Over by ₹${fmt(-diff)}`);
      const save = root.querySelector(".save-split");
      if (save) save.disabled = Math.abs(diff) > 0.001 || rows.some((r) => !r.querySelector(".dt").value);
      rows.forEach((r) => {
        const p = r.querySelector(".pct"), a = (r.querySelector(".amt").value || "").replace(/,/g, "");
        if (!pctMode() && p) p.value = t && a ? Math.round((parseFloat(a) / t) * 10000) / 100 : "";
      });
    }
    parts.addEventListener("input", recalc);
    root.querySelectorAll("[name=mode], [name=new_total]").forEach((i) => i.addEventListener("input", recalc));
    root.querySelectorAll("[name=mode]").forEach((i) => i.addEventListener("change", recalc));
    root.querySelector(".add-part").addEventListener("click", () => {
      const tpl = parts.querySelector(".part.tpl");
      const row = tpl.cloneNode(true);
      row.classList.remove("tpl"); row.hidden = false;
      row.querySelectorAll("input").forEach((i) => (i.disabled = false));
      parts.appendChild(row); recalc();
    });
    parts.addEventListener("click", (ev) => {
      if (ev.target.closest(".del")) { ev.target.closest(".part").remove(); recalc(); }
    });
    recalc();
  }

  document.addEventListener("DOMContentLoaded", () => {
    init(document);
    // Scroll the grid sideways so today's column is in view (with a few days before it).
    const wrap = document.querySelector(".grid-wrap");
    const th = wrap && wrap.querySelector("thead th.is-today");
    const head = wrap && wrap.querySelector("thead th.rowhead");
    if (th && head && th.offsetLeft + th.offsetWidth > wrap.clientWidth) {
      wrap.scrollLeft = Math.max(0, th.offsetLeft - head.offsetWidth - th.offsetWidth * 3);
    }
  });
})();
