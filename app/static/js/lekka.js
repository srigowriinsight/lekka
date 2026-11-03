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
    document.dispatchEvent(new CustomEvent("lekka:opened", { detail: d }));
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

  // Revenue shared with a partner company: below 100% ask for the partner's expense and show its amount.
  document.addEventListener("input", (ev) => {
    const f = ev.target.form;
    if (!f || !(ev.target.matches(".js-share") || ev.target.name === "amount" || ev.target.name === "title")) return;
    const box = f.querySelector(".js-share");
    if (!box) return;
    const pct = parseFloat((box.value || "100").replace("%", ""));
    const amtBox = f.querySelector('[name="amount"]');
    const amt = parseFloat(((amtBox && amtBox.value) || box.dataset.total || "0").replace(/[,₹\s]/g, "")) || 0;
    const extra = f.querySelector(".share-extra");
    const shared = !isNaN(pct) && pct < 100;
    if (extra) extra.hidden = !shared;
    const none = f.querySelector(".share-none");
    if (none) none.hidden = shared;
    if (shared) {
      const part = Math.round(amt * (100 - pct)) / 100;
      f.querySelectorAll(".js-share-amt").forEach((el) => {
        el.textContent = "₹" + part.toLocaleString("en-IN", { maximumFractionDigits: 2 });
      });
      const t = f.querySelector('[name="share_title"]');
      const tb = f.querySelector('[name="title"]');
      const title = tb ? tb.value.trim() : "";
      if (t) t.placeholder = "Partner share – " + (title || "(title)");
    }
  });
  // Price list: GST calculator. Price before GST x GST % = GST (half CGST, half SGST); price + GST = total.
  // With "Price includes GST" ticked, the typed price is the total and the price before GST is worked back.
  function gstCalc(row) {
    const val = (n) => parseFloat(((row.querySelector(`[name="${n}"]`) || {}).value || "").replace(/[,₹%\s]/g, ""));
    const paise = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
    const fmt = (x) => x.toLocaleString("en-IN", { minimumFractionDigits: x % 1 ? 2 : 0, maximumFractionDigits: 2 });
    const rate = val("gst_rate"), typed = val("price");
    const inc = row.querySelector('[name="includes_gst"]');
    const ok = !isNaN(rate) && !isNaN(typed) && rate >= 0 && typed >= 0;
    const base = ok ? paise(inc && inc.checked ? (typed * 100) / (100 + rate) : typed) : 0;
    const gst = ok ? paise((base * rate) / 100) : 0;
    const half = paise(gst / 2);
    const set = (sel, text) => row.querySelectorAll(sel).forEach((el) => (el.textContent = text));
    set(".js-gst-v", fmt(gst));
    set(".js-total-v", fmt(paise(base + gst)));
    set(".js-split", `CGST ₹${fmt(half)} + SGST ₹${fmt(paise(gst - half))}`);
    set(".js-base-v", fmt(base));
    row.querySelectorAll(".js-base").forEach((el) => (el.hidden = !(inc && inc.checked)));
  }
  ["input", "change"].forEach((t) => document.addEventListener(t, (ev) => {
    const row = ev.target.closest && ev.target.closest(".js-gst");
    if (row) gstCalc(row);
    if (ev.target.matches && ev.target.matches(".js-price-search")) {
      const q = ev.target.value.trim().toLowerCase();
      const scope = ev.target.dataset.target || ".price-list";
      document.querySelectorAll(`${scope} tbody tr[data-product]`).forEach((tr) => (tr.hidden = !!q && !tr.dataset.product.includes(q)));
    }
  }));
  document.addEventListener("DOMContentLoaded", () => document.querySelectorAll("form.js-gst").forEach(gstCalc));
  // New revenue: picking a price-list product (and a quantity) fills the amount = price incl. GST x qty,
  // the title if it is empty, and a remark saying what was sold. Everything stays editable.
  function productFill(f) {
    const sel = f.querySelector(".js-product"), qtyBox = f.querySelector(".js-qty");
    const note = f.querySelector(".js-product-note");
    const o = sel && sel.selectedOptions[0];
    if (qtyBox) qtyBox.disabled = !(o && o.value);
    if (!o || !o.value) { if (note) note.hidden = true; return; }
    const price = parseFloat(o.dataset.total);
    let qty = parseFloat((qtyBox && qtyBox.value) || "1");
    if (isNaN(qty) || qty < 0) qty = 0;
    const amount = Math.round(price * qty * 100) / 100;
    const fmt = (x) => x.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    const amt = f.querySelector('[name="amount"]'), title = f.querySelector('[name="title"]');
    const rem = f.querySelector('[name="remarks"]');
    if (amt) { amt.value = Number.isInteger(amount) ? String(amount) : amount.toFixed(2); amt.dispatchEvent(new Event("input", { bubbles: true })); }
    if (title && (!title.value.trim() || title.dataset.auto === "1")) {
      title.value = o.dataset.name; title.dataset.auto = "1"; title.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const line = `${fmt(qty)} × ${o.dataset.name} @ ₹${fmt(price)} incl. GST`;
    if (rem && (!rem.value.trim() || rem.dataset.auto === "1")) { rem.value = line; rem.dataset.auto = "1"; }
    if (note) { note.hidden = false; note.textContent = `Amount = ₹${fmt(price)} × ${fmt(qty)} = ₹${fmt(amount)} (price incl. GST × qty). You can still change it.`; }
  }
  document.addEventListener("change", (ev) => {
    if (ev.target.matches && ev.target.matches(".js-product")) productFill(ev.target.form);
  });
  document.addEventListener("input", (ev) => {
    if (!ev.target.matches) return;
    if (ev.target.matches(".js-qty")) productFill(ev.target.form);
    // typed by hand: stop replacing it
    if (ev.isTrusted && (ev.target.name === "title" || ev.target.name === "remarks")) delete ev.target.dataset.auto;
  });
  // a popup that opens with a share box already filled (Edit of a shared revenue): show its figures at once
  document.addEventListener("lekka:opened", (ev) => {
    const box = ev.detail && ev.detail.querySelector(".js-share");
    if (box) box.dispatchEvent(new Event("input", { bubbles: true }));
  });
  // keep the partner-share date in step with the revenue date until it is changed by hand
  document.addEventListener("change", (ev) => {
    if (ev.target.name === "share_date") ev.target.dataset.touched = "1";
    if (ev.target.name !== "planned_date" || !ev.target.form) return;
    const sd = ev.target.form.querySelector('[name="share_date"]');
    if (sd && !sd.dataset.touched) sd.value = ev.target.value;
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
        if (data.open) { restore(); openDialog(data.open); return; }   // e.g. ask the partner share % again
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
    // Opens or closes what sits directly under a head: its sub-heads and its own item lines.
    // Closing also closes everything further down, so nothing is left showing under a closed head.
    const rows = document.querySelectorAll(`tr[data-parent="${key}"]`);
    const btn = document.querySelector(`[data-toggle="${key}"]`);
    const open = force !== undefined ? force : (rows[0] && rows[0].hidden);
    rows.forEach((r) => {
      r.hidden = !open;
      if (!open && r.dataset.key) toggleHead(r.dataset.key, false);
    });
    if (btn) btn.textContent = open ? "▾" : "▸";
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
    // parents come first in the page, so opening in page order opens every level
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
  // Recurring template form: only offer heads that fit the chosen direction (revenue / expense / both).
  function headsByDirection(root) {
    root.querySelectorAll("select.js-heads-by-direction").forEach((sel) => {
      const dirSel = sel.form && sel.form.querySelector('[name="direction"]');
      if (!dirSel) return;
      const apply = () => {
        const d = dirSel.value;
        sel.querySelectorAll("option[data-kind]").forEach((o) => {
          const ok = o.dataset.kind === "BOTH" || o.dataset.kind === d;
          o.hidden = !ok; o.disabled = !ok;
          if (!ok && o.selected) sel.value = "";
        });
      };
      dirSel.addEventListener("change", apply);
      apply();
    });
  }

  function init(root) {
    headsByDirection(root);
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
        // shared revenue: show what this part's partner share comes to (same %)
        const sh = r.querySelector(".sh-amt"), frac = parseFloat(parts.dataset.shareFraction || "");
        if (sh && !isNaN(frac)) sh.textContent = a ? `Share ₹${fmt(Math.round(parseFloat(a) * frac * 100) / 100)}` : "";
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
