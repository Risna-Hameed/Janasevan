/* Panchayat Dashboard front end. Plain JavaScript; every action goes to the server, and the
   screen only changes after the server confirms the database was updated. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "-");
  const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "-");
  const badge = (status, label) => `<span class="badge-s s-${esc(status)}">${esc(label || status.replace(/_/g, " "))}</span>`;
  const can = (perm) => S.me && S.me.official.permissions.includes(perm);
  const loading = (el) => { el.innerHTML = `<div class="loading">Loading…</div>`; };
  const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;

  const S = { me: null, charts: {}, req: { page: 1 }, hh: { page: 1 }, hi: { page: 1 }, sv: { status: "" }, current: null };

  /* ---------- server calls ---------- */
  async function api(method, url, body) {
    const opts = { method, credentials: "same-origin", headers: {} };
    if (method !== "GET" && S.me) opts.headers["X-CSRF-Token"] = S.me.csrf_token;
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, opts); } catch (e) { throw new Error("Could not reach the server."); }
    if (res.status === 401 && !url.endsWith("/login")) { showLogin(); throw new Error("Your session has ended. Please sign in again."); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.error || `Error ${res.status}`); err.data = data; err.status = res.status; throw err; }
    return data;
  }

  function toast(msg, kind = "ok") {
    const el = document.createElement("div");
    el.className = `toast-pj ${kind}`; el.setAttribute("role", kind === "err" ? "alert" : "status"); el.textContent = msg;
    $("#toasts").appendChild(el); setTimeout(() => el.remove(), 4500);
  }

  /* ---------- sign-in ---------- */
  function showLogin() {
    S.me = null; $("#appView").hidden = true; $("#loginView").hidden = false; $("#lgUser").focus();
  }
  async function doLogin(e) {
    e.preventDefault();
    $("#lgError").hidden = true; $("#lgBtn").disabled = true;
    try {
      S.me = await api("POST", "/api/panchayat/login", { username: $("#lgUser").value, password: $("#lgPass").value });
      $("#lgPass").value = ""; startApp();
    } catch (err) { $("#lgError").textContent = err.message; $("#lgError").hidden = false; }
    finally { $("#lgBtn").disabled = false; }
  }
  async function doLogout() {
    try { await api("POST", "/api/panchayat/logout"); } catch (e) { /* already signed out */ }
    location.hash = ""; showLogin();
  }

  function startApp() {
    const me = S.me;
    $("#loginView").hidden = true; $("#appView").hidden = false;
    $("#tbPanchayat").textContent = me.panchayat.name;
    $("#tbBlock").textContent = `${me.panchayat.block} · ${me.panchayat.district}${me.all_wards ? "" : " · Wards " + me.wards.map((w) => w.number).join(", ")}`;
    $("#tbUser").textContent = me.official.full_name; $("#tbRole").textContent = me.official.role_label;
    $("#tbDate").textContent = new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "long", year: "numeric" });
    $("#demoStrip").hidden = !me.panchayat.is_demo;
    $$("[data-perm]").forEach((el) => { el.hidden = !can(el.dataset.perm); });
    const wardOpts = `<option value="">All</option>` + me.wards.map((w) => `<option value="${w.number}">${w.number} - ${esc(w.name)}</option>`).join("");
    ["#fward", "#hward", "#award"].forEach((s) => { $(s).innerHTML = wardOpts; });
    const catOpts = `<option value="">All</option>` + Object.entries(me.categories).map(([k, v]) => `<option value="${k}">${esc(v.name_en)}</option>`).join("");
    $("#fcat").innerHTML = catOpts; $("#acat").innerHTML = catOpts;
    $("#fstatus").innerHTML = `<option value="open">Open</option><option value="">All</option><option value="closed">Closed</option>` +
      Object.entries(me.statuses).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    refreshCounters();
    route();
  }

  async function refreshCounters() {
    try {
      const n = await api("GET", "/api/panchayat/notifications");
      $("#navNotif").textContent = n.unread || ""; $("#tbNotif").textContent = n.unread; $("#tbNotif").hidden = !n.unread;
      if (can("view_requests")) {
        const open = await api("GET", "/api/panchayat/verification-requests?status=open&page_size=5");
        $("#navOpen").textContent = open.total || "";
      }
    } catch (e) { /* counters are optional */ }
  }

  /* ---------- router ---------- */
  const VIEWS = { overview: renderOverview, requests: renderRequests, households: renderHouseholds, surveys: renderSurveys,
                  analytics: renderAnalytics, history: renderHistory, notifications: renderNotifications, admin: renderAdmin };
  function route() {
    if (!S.me) return;
    let view = location.hash.slice(1) || "overview";
    if (!VIEWS[view]) view = "overview";
    const link = $(`[data-nav="${view}"]`);
    if (link && link.hidden) view = "overview";
    $$("[data-view]").forEach((s) => { s.hidden = s.dataset.view !== view; });
    $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === view));
    $("#sidebar").classList.remove("open"); $("#menuBtn").setAttribute("aria-expanded", "false");
    VIEWS[view]();
    $("#content").focus({ preventScroll: true });
  }

  /* ---------- overview ---------- */
  async function renderOverview() {
    loading($("#ovStats"));
    try {
      const d = await api("GET", "/api/panchayat/dashboard");
      const s = d.stats;
      const cards = [["Total households", s.households], ["Total individuals", s.individuals], ["Wards configured", s.wards, "info"],
        ["New requests", s.new_requests, "info"], ["Awaiting review", s.awaiting_review, "info"], ["Additional documents needed", s.info_required, "warn"],
        ["Verified", s.verified], ["Unsuccessful / referred", s.unsuccessful_or_referred, "warn"],
        [`Pending > ${S.me.processing_target_days} days`, s.overdue, s.overdue ? "bad" : ""]];
      $("#ovStats").innerHTML = cards.map(([l, n, k]) => `<div class="stat ${k || ""}"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`).join("");
      $("#ovNote").textContent = d.data_note;
      $("#ovRecent").innerHTML = miniTable(d.recent_requests);
      $("#ovAttention").innerHTML = d.attention.length ? miniTable(d.attention) : empty("Nothing overdue or resubmitted. 👍");
      $("#ovActivity").innerHTML = d.activity.length ? `<table class="table table-sm table-pj"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th></tr></thead><tbody>
        ${d.activity.map((a) => `<tr><td>${fmtDate(a.created_at)}</td><td>${esc(a.actor)}</td><td>${esc(a.action.replace(/_/g, " "))}</td><td class="mono">${esc((a.target_type || "") + " " + (a.target_id || ""))}</td></tr>`).join("")}</tbody></table>` : empty("No activity yet.");
      drawCharts(d.charts);
    } catch (err) { $("#ovStats").innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`; }
  }
  function miniTable(rows) {
    if (!rows.length) return empty("No requests yet.");
    return `<div class="table-responsive"><table class="table table-sm table-pj table-hover"><thead><tr><th>Request</th><th>Ward</th><th>Category</th><th>Submitted</th><th>Status</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="${r.overdue ? "overdue" : ""}"><td><a href="#" class="mono" data-open-req="${r.id}">${esc(r.request_ref)}</a></td><td>${r.ward}</td>
      <td>${esc(r.category_en)}</td><td>${fmtDay(r.submitted_at)}</td><td>${badge(r.status, r.status_en)}${r.overdue ? " " + badge("overdue", "overdue") : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }
  // Short legend names so labels fit inside the small chart blocks (full name stays in the tooltip).
  const STATUS_SHORT = { "Queued for review": "Queued", "Under review": "Reviewing", "Additional information required": "Info needed",
                         "Verification completed": "Verified", "Verification unsuccessful": "Unsuccessful",
                         "Referred to another authority": "Referred" };
  const CAT_SHORT = { "Residence proof": "Residence", "Income certificate": "Income", "Educational certificate": "Education",
                      "Community / category certificate": "Community", "Non-creamy-layer certificate": "Non-creamy",
                      "Other supporting document": "Other" };

  function drawCharts(c) {
    if (!window.Chart || !c) return;
    const colors = ["#1f5e3b", "#d79a1c", "#1d4f86", "#a8271f", "#6fb08a", "#7b5ea7", "#78909c", "#e07b39"];
    const cut = (l, n = 12) => (String(l).length > n ? String(l).slice(0, n - 1) + "…" : String(l));
    const round = (type) => type === "doughnut" || type === "pie";

    const make = (id, type, labels, values, shortMap = {}, opts = {}) => {
      try {
        const el = $(id); if (!el) return;
        if (S.charts[id]) S.charts[id].destroy();
        const short = (l) => cut(shortMap[l] || l);
        const box = el.parentElement, old = box.querySelector(".chart-empty");
        if (old) old.remove();
        const horizontal = opts.indexAxis === "y";
        const valueAxis = { beginAtZero: true, suggestedMax: 4, ticks: { precision: 0, font: { size: 9 }, maxTicksLimit: 4 }, grid: { color: "#eef1ef" } };
        const labelAxis = { grid: { display: false }, ticks: { font: { size: 9 }, autoSkip: true, maxRotation: 0,
                            callback(v) { return short(this.getLabelForValue(v)); } } };
        S.charts[id] = new Chart(el, {
          type,
          data: { labels, datasets: [{ data: values,
            backgroundColor: type === "line" ? "#1f5e3b22" : colors,
            borderColor: round(type) ? "#fff" : "#1f5e3b", borderWidth: round(type) ? 2 : (type === "line" ? 2 : 0),
            fill: type === "line", tension: .3, pointRadius: 2, maxBarThickness: 22, borderRadius: type === "bar" ? 3 : 0 }] },
          options: { responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: 2 },
            cutout: type === "doughnut" ? "58%" : undefined,
            plugins: { legend: { display: round(type), position: "right",
                       labels: { boxWidth: 8, padding: 5, font: { size: 9 },
                                 generateLabels: (ch) => Chart.overrides[type].plugins.legend.labels.generateLabels(ch)
                                   .map((x) => ({ ...x, text: `${short(x.text)} (${values[x.index] ?? 0})` })) } } },
            scales: round(type) ? {} : (horizontal ? { x: valueAxis, y: labelAxis } : { x: labelAxis, y: valueAxis }),
            ...opts } });
        if (!values.length || !values.some((v) => v > 0))
          box.insertAdjacentHTML("beforeend", '<div class="empty chart-empty position-absolute top-50 start-50 translate-middle">No data yet</div>');
      } catch (e) { console.error("chart", id, e); }   // one bad chart never blanks the others
    };

    const obj = (o) => o || {};
    make("#chStatus", "doughnut", Object.keys(obj(c.by_status)), Object.values(obj(c.by_status)), STATUS_SHORT);
    make("#chCategory", "pie", Object.keys(obj(c.by_category)), Object.values(obj(c.by_category)), CAT_SHORT);
    make("#chWard", "bar", Object.keys(obj(c.by_ward)), Object.values(obj(c.by_ward)));
    const act = c.activity || { labels: [], values: [] };
    make("#chActivity", "line", act.labels.map((d) => new Date(d).toLocaleDateString("en-IN", { day: "numeric", month: "short" })), act.values);
  }

  /* ---------- requests ---------- */
  async function renderRequests() {
    const box = $("#reqTable"); loading(box);
    const q = new URLSearchParams({ q: $("#fq").value, ward: $("#fward").value, category: $("#fcat").value, status: $("#fstatus").value,
                                    from: $("#ffrom").value, to: $("#fto").value, sort: $("#fsort").value, page: S.req.page, page_size: 10 });
    try {
      const d = await api("GET", "/api/panchayat/verification-requests?" + q);
      box.innerHTML = d.items.length ? `<table class="table table-pj table-hover"><thead><tr><th>Request ID</th><th>Citizen ref</th><th>Applicant</th><th>Ward</th>
        <th>Document</th><th>Purpose</th><th>Submitted</th><th>Status</th><th>Assigned</th><th></th></tr></thead><tbody>
        ${d.items.map((r) => `<tr class="${r.overdue ? "overdue" : ""}"><td class="mono">${esc(r.request_ref)}</td><td class="mono">${esc(r.citizen_ref)}</td>
          <td>${esc(r.applicant_name)}</td><td>${r.ward}</td><td>${esc(r.category_en)}</td><td class="small">${esc(r.purpose)}</td>
          <td>${fmtDay(r.submitted_at)}</td><td>${badge(r.status, r.status_en)}${r.resubmission_count ? ` <span class="badge-s s-submitted">resubmitted</span>` : ""}${r.overdue ? " " + badge("overdue", "overdue") : ""}</td>
          <td class="small">${esc(r.assigned_official || "-")}</td>
          <td><button class="btn btn-sm btn-outline-pj" data-open-req="${r.id}">${["queued_for_review", "under_review"].includes(r.status) && can("review_requests") ? "Review" : "Open"}</button></td></tr>`).join("")}
        </tbody></table>` : empty("No requests match these filters.");
      pager($("#reqPager"), d, (p) => { S.req.page = p; renderRequests(); });
    } catch (err) { box.innerHTML = `<div class="alert alert-danger m-3">${esc(err.message)}</div>`; }
  }
  function pager(el, d, go) {
    if (d.pages <= 1) { el.innerHTML = d.total ? `<span class="muted">${d.total} records</span>` : ""; return; }
    el.innerHTML = `<span class="muted me-2">${d.total} records · page ${d.page} of ${d.pages}</span>
      <button class="btn btn-sm btn-outline-secondary" ${d.page <= 1 ? "disabled" : ""} data-p="${d.page - 1}">‹ Prev</button>
      <button class="btn btn-sm btn-outline-secondary" ${d.page >= d.pages ? "disabled" : ""} data-p="${d.page + 1}">Next ›</button>`;
    $$("[data-p]", el).forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.p))));
  }

  /* ---------- review workspace ---------- */
  async function openReview(id) {
    const modal = bootstrap.Modal.getOrCreateInstance($("#reviewModal"));
    $("#rvBody").innerHTML = `<div class="loading">Loading request…</div>`; modal.show();
    try {
      const d = await api("GET", `/api/panchayat/verification-requests/${id}`);
      S.current = d;
      renderReview(d);
    } catch (err) { $("#rvBody").innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`; }
  }
  function renderReview(d) {
    const r = d.request, a = d.applicant, latestDraft = d.reviews.find((x) => x.kind === "draft");
    $("#rvTitle").innerHTML = `${esc(r.request_ref)} ${badge(r.status, r.status_en)}`;
    const docs = d.documents;
    const docTabs = docs.map((doc, i) => `<button class="btn btn-sm ${i === docs.length - 1 ? "btn-pj" : "btn-outline-secondary"}" data-doc="${doc.id}" data-mime="${esc(doc.mime_type)}">
      ${doc.round > 1 ? `Resubmission ${doc.round - 1}` : "Original"} · ${esc(doc.mime_type.split("/")[1].toUpperCase())}</button>`).join(" ");
    const acts = d.actions;
    $("#rvBody").innerHTML = `
      <div class="rv-grid">
        <div>
          <div class="rv-box"><h3>Document</h3>
            <div class="d-flex flex-wrap gap-2 mb-2">${docTabs || "<span class='muted'>No document</span>"}</div>
            <div class="viewer" id="viewer"><span class="text-white-50">Select a document</span></div>
            <div class="d-flex justify-content-between small mt-2 flex-wrap gap-2"><span id="docMeta" class="muted"></span>
              ${can("download_documents") ? `<a id="docDownload" class="btn btn-sm btn-outline-secondary" href="#" hidden>Download (logged)</a>` : ""}</div>
          </div>
          <div class="rv-box ai-box"><h3>AI review · assistance only</h3>
            <div id="aiBox"><p class="small muted mb-2">Runs OCR and rule checks against the household record. The result is advice for you, not a decision.</p>
              ${can("review_requests") ? `<button class="btn btn-sm btn-outline-primary" id="runAi">Run AI pre-check</button>` : ""}</div>
          </div>
          <div class="rv-box"><h3>History</h3><ul class="timeline">${d.history.map((h) => `<li><strong>${esc(h.to_status.replace(/_/g, " "))}</strong>
            <span class="muted">· ${fmtDate(h.created_at)} · ${esc(h.official || h.actor_type)}</span>${h.note ? `<div class="small">${esc(h.note)}</div>` : ""}</li>`).join("")}</ul></div>
        </div>
        <div>
          <div class="rv-box"><h3>Applicant</h3><dl class="rv-dl">
            <dt>Citizen ref</dt><dd class="mono">${esc(a.citizen_ref)}</dd><dt>Name</dt><dd>${esc(a.name || "-")}</dd>
            <dt>Household</dt><dd class="mono">${esc(a.household_ref)}</dd><dt>Ward</dt><dd>${esc(a.ward)}</dd>
            <dt>Requested</dt><dd>${fmtDate(a.request_date)}</dd><dt>Purpose</dt><dd>${esc(r.purpose)}${r.scheme_id ? ` <span class="muted small">(${esc(r.scheme_id)})</span>` : ""}</dd>
            ${r.citizen_remarks ? `<dt>Citizen note</dt><dd>${esc(r.citizen_remarks)}</dd>` : ""}</dl></div>
          <div class="rv-box"><h3>Document information</h3><dl class="rv-dl" id="docInfo"></dl>
            <div class="scope-note mt-2">${esc(d.category.note_en || "")}${d.category.scope === "refer_only" ? ` <strong>The panchayat cannot verify this type - refer it.</strong>` : ""}</div></div>
          <div class="rv-box official-box"><h3>Official review</h3>
            ${acts.length === 0 ? `<p class="mb-0">${r.status === "queued_for_review" || r.status === "under_review"
                ? (can("review_requests") ? "This request is assigned to another official." : "Your role can view but not act on requests.")
                : `This request is closed (${esc(r.status_en)}).`}</p>${r.official_remarks ? `<p class="small mt-2 mb-0"><strong>Remarks shown to citizen:</strong> ${esc(r.official_remarks)}</p>` : ""}
                ${r.status === "verified" ? `<a class="btn btn-sm btn-outline-pj mt-2" href="/api/panchayat/verification-requests/${r.id}/receipt">Download verification record</a>` : ""}` : ""}
            ${acts.includes("start_review") ? `<p class="small">Start the review to assign it to yourself. The citizen will see "Under review".</p>
                <button class="btn btn-pj" data-act="start_review">Start review</button>` : ""}
            ${acts.includes("save_draft") ? `
              <p class="small fw-semibold mb-1">Verification checklist</p>
              <div id="checklist">${d.checklist.map((c) => `<label class="check-row"><input type="checkbox" value="${c.id}" ${latestDraft && latestDraft.checklist[c.id] ? "checked" : ""}> ${esc(c.label_en)}</label>`).join("")}</div>
              <label class="form-label small mt-2" for="rvRemarks">Remarks (visible to citizen)</label>
              <textarea class="form-control form-control-sm" id="rvRemarks" rows="2" maxlength="1000">${esc(latestDraft ? latestDraft.remarks || "" : "")}</textarea>
              <label class="form-label small mt-2" for="rvNotes">Internal notes (not shown to citizen)</label>
              <textarea class="form-control form-control-sm" id="rvNotes" rows="2" maxlength="1000">${esc(latestDraft ? latestDraft.internal_notes || "" : "")}</textarea>
              <label class="form-label small mt-2" for="rvOffRef">Official reference (optional)</label>
              <input class="form-control form-control-sm" id="rvOffRef" maxlength="80">
              <div class="d-flex flex-wrap gap-2 mt-3">
                ${acts.includes("verify") ? `<button class="btn btn-pj" data-act="verify">Mark as verified</button>` : ""}
                <button class="btn btn-outline-warning" data-act="request_correction">Request documents</button>
                <button class="btn btn-outline-secondary" data-act="refer">Refer</button>
                <button class="btn btn-outline-danger" data-act="mark_unsuccessful">Unsuccessful</button>
                <button class="btn btn-link" data-act="save_draft">Save draft</button>
              </div>
              <p class="small muted mt-2 mb-0">Verifying records an administrative finding only. It does not issue a certificate or approve any scholarship.</p>` : ""}
          </div>
        </div>
      </div>`;
    if (docs.length) showDoc(docs[docs.length - 1]);
    $$("[data-doc]").forEach((b) => b.addEventListener("click", () => {
      $$("[data-doc]").forEach((x) => { x.className = "btn btn-sm btn-outline-secondary"; }); b.className = "btn btn-sm btn-pj";
      showDoc(docs.find((x) => String(x.id) === b.dataset.doc));
    }));
    const ai = $("#runAi"); if (ai) ai.addEventListener("click", runAi);
    $$("[data-act]", $("#rvBody")).forEach((b) => b.addEventListener("click", () => reviewAction(b.dataset.act)));
  }
  async function showDoc(doc) {
    const v = $("#viewer");
    v.innerHTML = `<div class="loading text-white-50">Loading document…</div>`;
    $("#docInfo").innerHTML = `<dt>Type</dt><dd>${esc(S.current.category.name_en)}</dd><dt>Reference no.</dt><dd>${esc(doc.doc_reference || "-")}</dd>
      <dt>Issued by</dt><dd>${esc(doc.issuing_authority || "-")}</dd><dt>Valid until</dt><dd>${esc(doc.valid_until || "not stated")}</dd>
      <dt>Uploaded</dt><dd>${fmtDate(doc.uploaded_at)}</dd><dt>File</dt><dd>${esc(doc.mime_type)} · ${(doc.size_bytes / 1024).toFixed(0)} KB</dd>`;
    $("#docMeta").textContent = doc.round > 1 ? `Resubmission round ${doc.round - 1}` : "Original submission";
    const dl = $("#docDownload"); if (dl) { dl.href = `/api/panchayat/documents/${doc.id}/preview?download=1`; dl.hidden = false; }
    try {
      const res = await fetch(`/api/panchayat/documents/${doc.id}/preview`, { credentials: "same-origin" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Preview unavailable");
      const url = URL.createObjectURL(await res.blob());
      v.innerHTML = doc.mime_type === "application/pdf" ? `<iframe src="${url}" title="Document preview"></iframe>` : `<img src="${url}" alt="Submitted document">`;
    } catch (err) { v.innerHTML = `<div class="text-white p-4 text-center">${esc(err.message)}<br><small>Use Download if your role allows it, or ask the citizen to resubmit.</small></div>`; }
  }
  async function runAi() {
    const box = $("#aiBox"); box.innerHTML = `<div class="loading">Running OCR and checks…</div>`;
    try {
      const r = await api("GET", `/api/panchayat/verification-requests/${S.current.request.id}/ai-review`);
      const ex = Object.entries(r.extracted || {});
      box.innerHTML = `<p class="small mb-1"><strong>${esc(r.label)}</strong> · ${esc(r.method)}</p>
        ${r.findings.map((f) => `<div class="finding f-${f.level}">${esc(f.text)}</div>`).join("")}
        ${ex.length ? `<p class="small mt-2 mb-1 fw-semibold">Extracted fields (proposed, unverified)</p><dl class="rv-dl">${ex.map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
        <p class="small muted mt-2 mb-0">${esc(r.disclaimer)}</p>`;
    } catch (err) { box.innerHTML = `<div class="text-danger small">${esc(err.message)}</div>`; }
  }
  function reviewState() {
    return { checklist: Object.fromEntries($$("#checklist input").map((i) => [i.value, i.checked])),
             remarks: ($("#rvRemarks") || {}).value || "", internal_notes: ($("#rvNotes") || {}).value || "",
             official_reference: ($("#rvOffRef") || {}).value || "" };
  }
  async function reviewAction(act) {
    const id = S.current.request.id, base = `/api/panchayat/verification-requests/${id}`, st = reviewState(), me = S.me;
    const done = async (msg) => { toast(msg); await openReview(id); refreshCounters(); route(); };
    try {
      if (act === "start_review") { await api("POST", base + "/start-review"); return done("Review started and assigned to you."); }
      if (act === "save_draft") { await api("POST", base + "/review", st); return toast("Draft notes saved. Nothing was sent to the citizen."); }
      if (act === "verify") {
        const missing = Object.entries(st.checklist).filter(([, v]) => !v);
        if (missing.length) return toast("Complete every checklist item before verifying.", "err");
        return formDialog("Confirm verification", `<p>You are recording that <strong>${esc(S.current.request.request_ref)}</strong> was checked and its details are consistent with panchayat records.</p>
          <ul class="small"><li>This creates a verification record the citizen can download.</li><li>It is <strong>not</strong> a certificate and does <strong>not</strong> approve any scholarship or benefit.</li></ul>
          <label class="check-row"><input type="checkbox" name="confirm" required> I confirm I have personally completed these checks.</label>`, "Confirm verification",
          async (f) => { if (!f.confirm.checked) throw new Error("Please tick the confirmation box.");
            const r = await api("POST", base + "/verify", { ...st, confirm: true }); await done(`Verified. Record ${r.receipt_ref} created and citizen notified.`); });
      }
      if (act === "request_correction") {
        return formDialog("Request additional documents", `<p class="small">The citizen will see your explanation and can upload a new file. Earlier files are kept.</p>
          ${Object.entries(me.correction_reasons).map(([k, v]) => `<label class="check-row"><input type="checkbox" name="items" value="${k}"> ${esc(v)}</label>`).join("")}
          <label class="form-label mt-2" for="crReason">Explain exactly what is needed</label><textarea class="form-control" id="crReason" name="reason" rows="3" required></textarea>`,
          "Send request", async (f) => {
            const items = $$("input[name=items]:checked", f).map((i) => i.value);
            await api("POST", base + "/request-correction", { missing_items: items, reason: f.reason.value, checklist: st.checklist });
            await done("Correction request sent to the citizen."); });
      }
      if (act === "mark_unsuccessful") {
        return formDialog("Mark verification unsuccessful", `<p class="small">Give a clear, specific reason. It will be shown to the citizen.</p>
          <label class="form-label" for="unCode">Reason category</label><select class="form-select" id="unCode" name="reason_code" required><option value="">Choose…</option>
          ${Object.entries(me.unsuccessful_reasons).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
          <label class="form-label mt-2" for="unReason">Explanation (at least 15 characters)</label><textarea class="form-control" id="unReason" name="reason" rows="3" required></textarea>
          <label class="check-row mt-2"><input type="checkbox" name="confirm"> I confirm this decision is based only on the documented checks.</label>`, "Record decision",
          async (f) => { if (!f.confirm.checked) throw new Error("Please tick the confirmation box.");
            await api("POST", base + "/unsuccessful", { reason_code: f.reason_code.value, reason: f.reason.value, confirm: true, checklist: st.checklist });
            await done("Decision recorded and citizen notified."); });
      }
      if (act === "refer") {
        return formDialog("Refer to another authority", `<label class="form-label" for="rfCat">Referral category</label><select class="form-select" id="rfCat" name="referral_category" required><option value="">Choose…</option>
          ${Object.entries(me.referral_categories).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
          <label class="form-label mt-2" for="rfAuth">Authority</label><input class="form-control" id="rfAuth" name="authority" value="${esc(S.current.category.refer_to || "")}" required>
          <label class="form-label mt-2" for="rfReason">Reason</label><textarea class="form-control" id="rfReason" name="reason" rows="3" required></textarea>`, "Refer request",
          async (f) => { await api("POST", base + "/refer", { referral_category: f.referral_category.value, authority: f.authority.value, reason: f.reason.value });
            await done("Request referred and citizen notified."); });
      }
    } catch (err) { toast(err.message, "err"); }
  }

  /* ---------- generic dialog ---------- */
  let fmHandler = null;
  function formDialog(title, html, submitLabel, onSubmit) {
    $("#fmTitle").textContent = title; $("#fmBody").innerHTML = html; $("#fmError").textContent = "";
    $("#fmSubmit").textContent = submitLabel; fmHandler = onSubmit;
    bootstrap.Modal.getOrCreateInstance($("#formModal")).show();
  }
  $("#fmForm").addEventListener("submit", async (e) => {
    e.preventDefault(); if (!fmHandler) return;
    $("#fmSubmit").disabled = true; $("#fmError").textContent = "";
    try { await fmHandler($("#fmForm")); bootstrap.Modal.getOrCreateInstance($("#formModal")).hide(); }
    catch (err) {
      const fields = err.data && err.data.fields ? " " + Object.entries(err.data.fields).map(([k, v]) => `${k}: ${v}`).join("; ") : "";
      $("#fmError").textContent = err.message + fields;
    } finally { $("#fmSubmit").disabled = false; }
  });

  /* ---------- households ---------- */
  async function renderHouseholds() {
    const box = $("#hhTable"); loading(box);
    const q = new URLSearchParams({ q: $("#hq").value, ward: $("#hward").value, status: $("#hstatus").value, page: S.hh.page });
    try {
      const d = await api("GET", "/api/panchayat/households?" + q);
      box.innerHTML = d.items.length ? `<table class="table table-pj table-hover"><thead><tr><th>Household ref</th><th>Ward</th><th>Survey ref</th><th>Survey date</th><th>Members</th><th>Status</th><th></th></tr></thead><tbody>
        ${d.items.map((h) => `<tr><td class="mono">${esc(h.household_ref)}</td><td>${h.ward}</td><td class="mono">${esc(h.survey_ref || "-")}</td><td>${fmtDay(h.survey_date)}</td>
          <td>${h.member_count}</td><td>${badge(h.validation_status)}</td><td><button class="btn btn-sm btn-outline-pj" data-open-hh="${h.id}">Open</button></td></tr>`).join("")}</tbody></table>`
        : empty("No households match.");
      pager($("#hhPager"), d, (p) => { S.hh.page = p; renderHouseholds(); });
    } catch (err) { box.innerHTML = `<div class="alert alert-danger m-3">${esc(err.message)}</div>`; }
  }
  async function openHousehold(id, sensitive = false) {
    try {
      const d = await api("GET", `/api/panchayat/households/${id}${sensitive ? "?sensitive=1" : ""}`);
      const h = d.household;
      $("#rvTitle").textContent = `Household ${h.household_ref}`;
      $("#rvBody").innerHTML = `
        <div class="rv-grid"><div>
          <div class="rv-box"><h3>Household summary</h3><dl class="rv-dl">
            <dt>Ward</dt><dd>${h.ward_number} - ${esc(h.ward_name)}</dd><dt>Address</dt><dd>${esc(h.address)}</dd>
            <dt>Survey</dt><dd class="mono">${esc(h.survey_ref || "-")} · ${fmtDay(h.survey_date)}</dd><dt>Members</dt><dd>${h.member_count}</dd>
            <dt>Housing</dt><dd>${esc(h.housing_condition || "-")}</dd><dt>Drinking water</dt><dd>${esc(h.drinking_water || "-")}</dd>
            <dt>Sanitation</dt><dd>${esc(h.sanitation || "-")}</dd><dt>Status</dt><dd>${badge(h.validation_status)}</dd>
            <dt>Annual income</dt><dd>${d.sensitive_shown ? (h.annual_income != null ? "₹" + Number(h.annual_income).toLocaleString("en-IN") : "-") : `<span class="muted">hidden</span>`}</dd></dl>
            ${!d.sensitive_shown && d.can_view_sensitive ? `<button class="btn btn-sm btn-outline-danger mt-2" id="showSensitive">Show sensitive details (logged)</button>` : ""}
            ${!d.can_view_sensitive ? `<p class="small muted mt-2 mb-0">Your role cannot open income or assistance details.</p>` : ""}</div>
          <div class="rv-box"><h3>Verification requests</h3>${d.verification_requests.length ? d.verification_requests.map((r) => `<div class="small">${esc(r.request_ref)} ${badge(r.status)}</div>`).join("") : "<span class='muted small'>None</span>"}</div>
        </div><div>
          <div class="rv-box"><h3>Members</h3><table class="table table-sm table-pj"><thead><tr><th>Ref</th><th>Name</th><th>Age</th><th>Education</th><th>Employment</th>${d.sensitive_shown ? "<th>Assistance</th>" : ""}</tr></thead><tbody>
            ${d.members.map((m) => `<tr><td class="mono small">${esc(m.member_ref.split("-").pop())}</td><td>${esc(m.name)}</td><td>${m.age ?? "-"}</td><td>${esc(m.education_status || "-")}</td>
              <td>${esc(m.employment_status || "-")}</td>${d.sensitive_shown ? `<td>${esc(m.assistance_needs || "-")}</td>` : ""}</tr>`).join("")}</tbody></table></div>
        </div></div>`;
      bootstrap.Modal.getOrCreateInstance($("#reviewModal")).show();
      const btn = $("#showSensitive"); if (btn) btn.addEventListener("click", () => openHousehold(id, true));
    } catch (err) { toast(err.message, "err"); }
  }

  /* ---------- surveys ---------- */
  async function renderSurveys() {
    const box = $("#svTable"); loading(box);
    try {
      const d = await api("GET", "/api/panchayat/surveys" + (S.sv.status ? `?status=${S.sv.status}` : ""));
      box.innerHTML = d.items.length ? `<table class="table table-pj table-hover"><thead><tr><th>Survey ref</th><th>Ward</th><th>Address</th><th>Members</th><th>Status</th><th>Entered by</th><th>Updated</th><th></th></tr></thead><tbody>
        ${d.items.map((s) => `<tr><td class="mono">${esc(s.survey_ref)}</td><td>${s.ward}</td><td class="small">${esc(s.address)}</td><td>${s.member_count}</td>
          <td>${badge(s.status, s.status.replace("submitted", "awaiting validation"))}${s.duplicate_of ? ` <span class="badge-s s-overdue">possible duplicate</span>` : ""}</td>
          <td class="small">${esc(s.created_by_name || "-")}</td><td>${fmtDay(s.updated_at)}</td><td><button class="btn btn-sm btn-outline-pj" data-open-sv="${s.id}">Open</button></td></tr>`).join("")}</tbody></table>`
        : empty("No survey records in this list.");
    } catch (err) { box.innerHTML = `<div class="alert alert-danger m-3">${esc(err.message)}</div>`; }
  }
  const OPT = { housing_condition: ["pucca", "semi_pucca", "kutcha", "damaged"], drinking_water: ["piped", "well", "public_tap", "none"], sanitation: ["own_toilet", "shared", "none"],
                education_status: ["", "not_enrolled", "school", "higher_secondary", "diploma", "undergraduate", "postgraduate", "completed"],
                employment_status: ["", "employed", "self_employed", "unemployed", "student", "homemaker", "retired", "not_applicable"] };
  const sel = (name, value, opts, extra = "") => `<select class="form-select form-select-sm" name="${name}" ${extra}>${opts.map((o) => `<option value="${o}" ${o === (value || "") ? "selected" : ""}>${o ? o.replace(/_/g, " ") : "—"}</option>`).join("")}</select>`;
  function memberRow(m = {}) {
    return `<div class="row g-1 member-row mb-1"><div class="col-4"><input class="form-control form-control-sm" name="m_name" placeholder="Name" value="${esc(m.name || "")}" aria-label="Member name"></div>
      <div class="col-2"><input class="form-control form-control-sm" name="m_age" placeholder="Age" value="${esc(m.age ?? "")}" aria-label="Age"></div>
      <div class="col-3">${sel("m_edu", m.education_status, OPT.education_status, 'aria-label="Education"')}</div>
      <div class="col-3">${sel("m_emp", m.employment_status, OPT.employment_status, 'aria-label="Employment"')}</div></div>`;
  }
  function surveyForm(data = {}, ward) {
    return `<div class="row g-2">
      <div class="col-md-3"><label class="form-label small" for="svWard">Ward</label><select class="form-select form-select-sm" id="svWard" name="ward" ${ward ? "disabled" : ""}>
        ${S.me.wards.map((w) => `<option value="${w.number}" ${w.number === ward ? "selected" : ""}>${w.number} - ${esc(w.name)}</option>`).join("")}</select></div>
      <div class="col-md-3"><label class="form-label small" for="svDate">Survey date</label><input type="date" class="form-control form-control-sm" id="svDate" name="survey_date" value="${esc(data.survey_date || "")}"></div>
      <div class="col-md-6"><label class="form-label small" for="svAddr">Address / location description</label><input class="form-control form-control-sm" id="svAddr" name="address" value="${esc(data.address || "")}" required></div>
      <div class="col-md-3"><label class="form-label small" for="svInc">Annual income (₹, if collected)</label><input class="form-control form-control-sm" id="svInc" name="annual_income" value="${esc(data.annual_income ?? "")}"></div>
      <div class="col-md-3"><label class="form-label small">Housing</label>${sel("housing_condition", data.housing_condition, ["", ...OPT.housing_condition])}</div>
      <div class="col-md-3"><label class="form-label small">Drinking water</label>${sel("drinking_water", data.drinking_water, ["", ...OPT.drinking_water])}</div>
      <div class="col-md-3"><label class="form-label small">Sanitation</label>${sel("sanitation", data.sanitation, ["", ...OPT.sanitation])}</div>
    </div>
    <p class="small fw-semibold mt-3 mb-1">Household members</p><div id="members">${(data.members && data.members.length ? data.members : [{}]).map(memberRow).join("")}</div>
    <button type="button" class="btn btn-sm btn-link" id="addMember">+ Add member</button>`;
  }
  function readSurveyForm(f) {
    const members = $$(".member-row", f).map((r) => ({ name: $("[name=m_name]", r).value.trim(), age: $("[name=m_age]", r).value.trim() || null,
      education_status: $("[name=m_edu]", r).value || null, employment_status: $("[name=m_emp]", r).value || null })).filter((m) => m.name);
    return { address: f.address.value, survey_date: f.survey_date.value || null, annual_income: f.annual_income.value || null,
             housing_condition: f.housing_condition.value || null, drinking_water: f.drinking_water.value || null, sanitation: f.sanitation.value || null, members };
  }
  function bindMembers() { const b = $("#addMember"); if (b) b.addEventListener("click", () => $("#members").insertAdjacentHTML("beforeend", memberRow())); }
  function newSurvey() {
    formDialog("New survey entry (draft)", surveyForm(), "Save draft", async (f) => {
      const r = await api("POST", "/api/panchayat/surveys", { ward: Number(f.ward.value), data: readSurveyForm(f) });
      toast(r.possible_duplicate ? `Saved ${r.survey_ref}. Possible duplicate of ${r.possible_duplicate.ref} - check before submitting.` : `Draft ${r.survey_ref} saved.`, r.possible_duplicate ? "err" : "ok");
      renderSurveys();
    });
    bindMembers();
  }
  async function openSurvey(id) {
    try {
      const d = await api("GET", `/api/panchayat/surveys/${id}`); const s = d.survey;
      const editable = ["draft", "needs_correction"].includes(s.status) && can("manage_surveys");
      const corr = d.corrections.length ? `<details class="mt-3"><summary class="small">Correction history (${d.corrections.length})</summary><ul class="small">${d.corrections.map((c) =>
        `<li>${fmtDate(c.created_at)} · ${esc(c.official || "")}: <strong>${esc(c.field)}</strong> ${esc(c.old_value)} → ${esc(c.new_value)}</li>`).join("")}</ul></details>` : "";
      const head = `<p>${badge(s.status, s.status.replace("submitted", "awaiting validation"))} <span class="mono small">${esc(s.survey_ref)}</span> · Ward ${s.ward}</p>
        ${s.reviewer_remarks ? `<div class="scope-note mb-2">Reviewer: ${esc(s.reviewer_remarks)}</div>` : ""}`;
      if (editable) {
        formDialog("Survey " + s.survey_ref, head + surveyForm(s.current_data, s.ward) + corr +
          `<label class="check-row mt-3"><input type="checkbox" name="submit_after"> Submit for validation after saving</label>`, "Save changes", async (f) => {
          await api("PATCH", `/api/panchayat/surveys/${id}`, { data: readSurveyForm(f) });
          if (f.submit_after.checked) await api("POST", `/api/panchayat/surveys/${id}/submit`);
          toast(f.submit_after.checked ? "Saved and submitted for validation." : "Changes saved (original values preserved)."); renderSurveys();
        });
        bindMembers();
      } else if (s.status === "submitted" && can("validate_surveys")) {
        const data = s.current_data;
        formDialog("Validate survey " + s.survey_ref, head + `<dl class="rv-dl"><dt>Address</dt><dd>${esc(data.address)}</dd><dt>Income</dt><dd>${esc(data.annual_income ?? "-")}</dd>
          <dt>Housing</dt><dd>${esc(data.housing_condition || "-")}</dd><dt>Water / sanitation</dt><dd>${esc(data.drinking_water || "-")} / ${esc(data.sanitation || "-")}</dd>
          <dt>Members</dt><dd>${data.members.map((m) => esc(`${m.name} (${m.age ?? "?"})`)).join(", ")}</dd></dl>${corr}
          <div class="mt-3"><label class="check-row"><input type="radio" name="decision" value="approve" checked> Approve - create the household record</label>
          <label class="check-row"><input type="radio" name="decision" value="return"> Return for correction</label>
          <label class="check-row"><input type="checkbox" name="not_dup"> I have checked this is not a duplicate household</label></div>
          <label class="form-label small mt-2" for="vRem">Remarks (required when returning)</label><textarea class="form-control form-control-sm" id="vRem" name="remarks" rows="2"></textarea>`,
          "Record decision", async (f) => {
            const dec = $$("input[name=decision]", f).find((r) => r.checked).value;
            const r = await api("POST", `/api/panchayat/surveys/${id}/validate`, { decision: dec, remarks: f.remarks.value, confirm_not_duplicate: f.not_dup.checked });
            toast(r.household_ref ? `Validated. Household ${r.household_ref} created.` : "Returned for correction."); renderSurveys();
          });
      } else {
        formDialog("Survey " + s.survey_ref, head + `<pre class="small bg-light p-2 rounded">${esc(JSON.stringify(s.current_data, null, 2))}</pre>` + corr, "Close", async () => {});
      }
    } catch (err) { toast(err.message, "err"); }
  }

  /* ---------- analytics ---------- */
  async function renderAnalytics() {
    const box = $("#anBody"); loading(box);
    const q = new URLSearchParams({ ward: $("#award").value, from: $("#afrom").value, to: $("#ato").value, category: $("#acat").value });
    try {
      const d = await api("GET", "/api/panchayat/analytics?" + q);
      const card = (l, n, k = "") => `<div class="stat ${k}"><div class="n">${n ?? "-"}</div><div class="l">${esc(l)}</div></div>`;
      const sr = d.survey_responses, pf = d.preliminary_flags, dq = d.data_quality, v = d.verification;
      box.innerHTML = `
        <div class="panel"><h2>Recorded survey responses</h2><div class="stat-grid">${card("Households surveyed", sr.households_surveyed)}${card("Individuals covered", sr.individuals_covered)}
          ${card("Students recorded", sr.students_recorded, "info")}${card("Households reporting unemployment", sr.households_reporting_unemployment, "warn")}</div></div>
        <div class="panel"><h2>Preliminary analytical flags</h2><p class="small muted">${esc(pf._note)}</p><div class="stat-grid">
          ${card("Housing assessment suggested", pf.housing_assessment_suggested, "warn")}${card("Water / sanitation needs reported", pf.infrastructure_needs_reported, "warn")}</div></div>
        <div class="panel"><h2>Data quality</h2><div class="stat-grid">${card("Incomplete survey records", dq.incomplete_survey_records, "warn")}${card("Awaiting validation", dq.records_awaiting_validation, "info")}</div></div>
        <div class="panel"><h2>Verification</h2><div class="stat-grid">${card("Average turnaround (days)", v.average_turnaround_days)}</div>
          <table class="table table-sm table-pj mt-3"><thead><tr><th>Ward</th><th>Households</th><th>Individuals</th><th>Verification requests</th></tr></thead><tbody>
          ${d.per_ward.map((w) => `<tr><td>${w.ward}</td><td>${w.households}</td><td>${w.individuals}</td><td>${w.requests}</td></tr>`).join("")}</tbody></table></div>
        <p class="small muted">${esc(d.note)}</p>`;
    } catch (err) { box.innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`; }
  }

  /* ---------- history ---------- */
  async function renderHistory() {
    const box = $("#hiTable"); loading(box);
    try {
      const d = await api("GET", `/api/panchayat/history?page=${S.hi.page}`);
      box.innerHTML = d.items.length ? `<table class="table table-pj table-hover"><thead><tr><th>When</th><th>Request</th><th>Category</th><th>From</th><th>To</th><th>By</th><th>Note</th></tr></thead><tbody>
        ${d.items.map((h) => `<tr><td>${fmtDate(h.at)}</td><td><a href="#" class="mono" data-open-req="${h.request_id}">${esc(h.request_ref)}</a></td><td>${esc(h.category_en)}</td>
          <td class="small">${esc(h.from)}</td><td>${badge(h.to_status, h.to)}</td><td class="small">${esc(h.by)}</td><td class="small">${esc(h.note || "")}</td></tr>`).join("")}</tbody></table>`
        : empty("No history yet.");
      pager($("#hiPager"), d, (p) => { S.hi.page = p; renderHistory(); });
    } catch (err) { box.innerHTML = `<div class="alert alert-danger m-3">${esc(err.message)}</div>`; }
  }

  /* ---------- notifications ---------- */
  async function renderNotifications() {
    const box = $("#ntList"); loading(box);
    try {
      const d = await api("GET", "/api/panchayat/notifications");
      box.innerHTML = d.items.length ? d.items.map((n) => `<div class="d-flex gap-3 py-2 border-bottom ${n.is_read ? "" : "fw-semibold"}">
        <span aria-hidden="true">${n.is_read ? "○" : "●"}</span><div class="flex-grow-1">${esc(n.title)} · ${esc(n.body)}<div class="small muted fw-normal">${fmtDate(n.created_at)}</div></div>
        ${n.request_id && can("view_requests") ? `<button class="btn btn-sm btn-outline-pj align-self-center" data-open-req="${n.request_id}">Open</button>` : ""}</div>`).join("")
        : empty("No notifications.");
    } catch (err) { box.innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`; }
  }

  /* ---------- administration ---------- */
  async function renderAdmin() {
    loading($("#adOfficials"));
    try {
      const [o, lic, au] = await Promise.all([api("GET", "/api/panchayat/admin/officials"), api("GET", "/api/panchayat/admin/licence"), api("GET", "/api/panchayat/admin/audit")]);
      $("#adOfficials").innerHTML = `<table class="table table-pj table-sm"><thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Wards</th><th>Last sign-in</th><th>Status</th><th></th></tr></thead><tbody>
        ${o.items.map((u) => `<tr><td class="mono">${esc(u.username)}</td><td>${esc(u.full_name)}</td><td>${esc(u.role_label)}</td><td>${esc(Array.isArray(u.wards) ? u.wards.join(", ") : "all")}</td>
          <td class="small">${fmtDate(u.last_login_at)}</td><td>${u.is_active ? badge("verified", "active") : badge("cancelled", "inactive")}</td>
          <td class="text-nowrap"><button class="btn btn-sm btn-link" data-wards="${u.id}" data-current="${esc(Array.isArray(u.wards) ? u.wards.join(",") : "")}">Wards</button>
          ${u.id !== S.me.official.id ? `<button class="btn btn-sm btn-link ${u.is_active ? "text-danger" : ""}" data-active="${u.id}" data-to="${u.is_active ? 0 : 1}">${u.is_active ? "Deactivate" : "Activate"}</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
      $("#adLicence").innerHTML = `<p>${badge(lic.valid ? "verified" : "unsuccessful", lic.status)} ${lic.expires_on ? `<span class="small muted">expires ${fmtDay(lic.expires_on)}</span>` : ""}</p>
        <p class="small mb-1">Features: ${lic.features.map((f) => `<span class="badge-s s-submitted">${esc(f)}</span>`).join(" ")}</p><p class="small muted mb-0">${esc(lic.note)}</p>`;
      $("#adRoles").innerHTML = Object.entries(o.roles).map(([k, v]) => `<p class="mb-1"><strong>${esc(v)}</strong><br><span class="muted">${o.permissions[k].map((p) => p.replace(/_/g, " ")).join(", ")}</span></p>`).join("");
      $("#adAudit").innerHTML = `<table class="table table-pj table-sm"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>IP</th></tr></thead><tbody>
        ${au.items.map((a) => `<tr><td class="small">${fmtDate(a.at)}</td><td>${esc(a.actor)}</td><td>${esc(a.action.replace(/_/g, " "))}</td><td class="mono small">${esc(a.target)}</td><td class="mono small">${esc(a.ip || "")}</td></tr>`).join("")}</tbody></table>`;
    } catch (err) { $("#adOfficials").innerHTML = `<div class="alert alert-danger">${esc(err.message)}</div>`; }
  }
  function newOfficial() {
    formDialog("Add official", `<div class="row g-2">
      <div class="col-md-6"><label class="form-label small" for="noUser">Username</label><input class="form-control" id="noUser" name="username" required></div>
      <div class="col-md-6"><label class="form-label small" for="noName">Full name</label><input class="form-control" id="noName" name="full_name" required></div>
      <div class="col-md-6"><label class="form-label small" for="noRole">Role</label><select class="form-select" id="noRole" name="role">
        <option value="verification_officer">Verification Officer</option><option value="data_entry">Survey Data Entry Operator</option>
        <option value="read_only">Read-Only Official</option><option value="panchayat_admin">Panchayat Administrator</option></select></div>
      <div class="col-md-6"><label class="form-label small" for="noPw">Temporary password (10+ chars, letters and numbers)</label><input class="form-control" id="noPw" name="password" type="password" autocomplete="new-password" required></div>
      <div class="col-12"><span class="form-label small d-block">Ward access (none ticked = all wards)</span>${S.me.wards.map((w) => `<label class="me-3 small"><input type="checkbox" name="wards" value="${w.number}"> Ward ${w.number}</label>`).join("")}</div></div>`,
    "Create account", async (f) => {
      await api("POST", "/api/panchayat/admin/officials", { username: f.username.value, full_name: f.full_name.value, role: f.role.value, password: f.password.value,
        wards: $$("input[name=wards]:checked", f).map((i) => Number(i.value)) });
      toast("Official account created."); renderAdmin();
    });
  }

  /* ---------- events ---------- */
  let debounce;
  function bind() {
    $("#loginForm").addEventListener("submit", doLogin);
    $("#logoutBtn").addEventListener("click", doLogout);
    $("#menuBtn").addEventListener("click", () => { const open = $("#sidebar").classList.toggle("open"); $("#menuBtn").setAttribute("aria-expanded", String(open)); });
    window.addEventListener("hashchange", route);
    $("#reqFilters").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { S.req.page = 1; renderRequests(); }, 300); });
    $("#reqFilters").addEventListener("submit", (e) => e.preventDefault());
    $("#hhFilters").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { S.hh.page = 1; renderHouseholds(); }, 300); });
    $("#hhFilters").addEventListener("submit", (e) => e.preventDefault());
    $("#anFilters").addEventListener("change", renderAnalytics);
    $$("#svTabs button").forEach((b) => b.addEventListener("click", () => { $$("#svTabs button").forEach((x) => x.classList.toggle("active", x === b)); S.sv.status = b.dataset.st; renderSurveys(); }));
    $("#newSurveyBtn").addEventListener("click", newSurvey);
    $("#newOfficialBtn").addEventListener("click", newOfficial);
    $("#readAllBtn").addEventListener("click", async () => { await api("POST", "/api/panchayat/notifications/read-all"); refreshCounters(); renderNotifications(); });
    document.addEventListener("click", async (e) => {
      const el = e.target.closest("[data-open-req],[data-open-hh],[data-open-sv],[data-wards],[data-active]");
      if (!el) return;
      e.preventDefault();
      if (el.dataset.openReq) openReview(el.dataset.openReq);
      if (el.dataset.openHh) openHousehold(el.dataset.openHh);
      if (el.dataset.openSv) openSurvey(el.dataset.openSv);
      if (el.dataset.wards) {
        const cur = el.dataset.current.split(",").filter(Boolean).map(Number);
        formDialog("Ward access", `<p class="small">Tick the wards this official may work on. None ticked = all wards.</p>${S.me.wards.map((w) => `<label class="me-3"><input type="checkbox" name="wards" value="${w.number}" ${cur.includes(w.number) ? "checked" : ""}> Ward ${w.number}</label>`).join("")}`,
          "Save", async (f) => { await api("POST", `/api/panchayat/admin/officials/${el.dataset.wards}/assignments`, { wards: $$("input[name=wards]:checked", f).map((i) => Number(i.value)) }); toast("Ward access updated."); renderAdmin(); });
      }
      if (el.dataset.active) {
        const on = el.dataset.to === "1";
        formDialog(on ? "Activate account" : "Deactivate account", `<p>${on ? "This official will be able to sign in again." : "This official will no longer be able to sign in. Their past actions stay in the audit log."}</p>`,
          on ? "Activate" : "Deactivate", async () => { await api("POST", `/api/panchayat/admin/officials/${el.dataset.active}/active`, { active: on }); toast("Account updated."); renderAdmin(); });
      }
    });
  }

  async function init() {
    bind();
    try { S.me = await api("GET", "/api/panchayat/me"); startApp(); } catch (e) { showLogin(); }
  }
  init();
})();
