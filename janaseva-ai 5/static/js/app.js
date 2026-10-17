/* JANASEVA AI front end (owned by P2). Plain JavaScript, no build step. */
(() => {
  const t = (k) => I18N.t(k);
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const L = (obj, base) => (I18N.lang === "ml" ? obj[base + "_ml"] : obj[base + "_en"]) || obj[base + "_en"] || "";

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked: memory only */ } },
  };

  const state = {
    profile: {},
    docs: new Set(store.get("janaseva_docs", [])),   // document ids the citizen says they have
    scheme: null, draftId: null, preset: "balanced", saved: new Set(), ocrFields: null,
  };
  const saveDocs = () => store.set("janaseva_docs", [...state.docs]);

  const PROFILE_LABEL = {
    name: "profile.name", age: "profile.age", state: "profile.state", district: "profile.district",
    education_level: "profile.education", course: "profile.course", institution_name: "profile.institutionName",
    institution_type: "profile.institution", year_of_study: "profile.year", course_mode: "profile.mode",
    institution_aicte_approved: "profile.aicte", annual_income: "profile.income", previous_exam_percentage: "profile.prevMarks",
    class12_percentile: "profile.percentile", gender: "profile.gender", community: "profile.community",
    disability_percent: "profile.disability", bpl: "profile.bpl", receives_other_scholarship: "profile.otherSch",
  };
  const BOOL_FIELDS = ["institution_aicte_approved", "bpl", "receives_other_scholarship"];
  const OPTIONAL_FIELDS = ["gender", "community", "disability_percent"];
  const fieldLabel = (f) => (PROFILE_LABEL[f] ? t(PROFILE_LABEL[f]) : f.replace(/_/g, " "));
  const showValue = (f, v) => {
    if (v === true) return t("opt.yes");
    if (v === false) return t("opt.no");
    if (["education_level", "institution_type", "course_mode", "gender", "community"].includes(f)) return t("opt." + v);
    if (f === "annual_income" && v !== null && v !== "") return "₹" + Number(v).toLocaleString("en-IN");
    return v;
  };

  function toast(msg) {
    const el = $("#toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { el.hidden = true; }, 2600);
  }
  const hasProfile = () => Object.keys(state.profile).some((k) => ["education_level", "annual_income", "state"].includes(k));

  /* ---------- router ---------- */
  const PAGES = ["home", "profile", "discover", "benefitiq", "apply", "verify", "dashboard", "about"];
  function route() {
    let page = (location.hash || "#home").slice(1);
    if (page === "how") page = "home";
    if (!PAGES.includes(page)) page = "home";
    PAGES.forEach((p) => { $("#" + p).hidden = p !== page; });
    $$(".navbar .nav-link").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + page));
    const nav = $("#nav"); if (nav.classList.contains("show")) bootstrap.Collapse.getOrCreateInstance(nav).hide();
    if (page === "discover") { renderBest(); renderSchemes(); }
    if (page === "dashboard") renderDashboard();
    if (page === "apply") renderApply();
    if (page === "benefitiq") renderBenefitIQ();
    if (page === "verify") renderVerify();
    if (location.hash.slice(1) === "how") { $("#how").scrollIntoView(); } else { window.scrollTo(0, 0); }
    $("#main").focus({ preventScroll: true });
  }

  /* ---------- home ---------- */
  function renderHome() {
    const feats = [["🔎", "feat.discovery"], ["✅", "feat.explain"], ["🗣️", "feat.lang"], ["📄", "feat.docs"], ["📊", "feat.ready"], ["🧭", "feat.guide"]];
    $("#featureCards").innerHTML = feats.map(([i, k]) => `
      <div class="col-md-6 col-lg-4"><div class="card-j feature"><div class="icon" aria-hidden="true">${i}</div>
      <h3 class="h5 mt-2">${esc(t(k))}</h3><p class="muted mb-0">${esc(t(k + ".d"))}</p></div></div>`).join("");
    $("#howSteps").innerHTML = [1, 2, 3, 4, 5, 6].map((n) => `<li>${esc(t("how." + n))}</li>`).join("");
  }

  /* ---------- profile ---------- */
  function fillSelects() {
    $$("select[data-options]").forEach((sel) => {
      const current = sel.value;
      sel.innerHTML = `<option value="">${esc(t("profile.choose"))}</option>` +
        sel.dataset.options.split(",").map((v) => `<option value="${esc(v)}">${esc(t("opt." + v))}</option>`).join("");
      sel.value = current;
    });
  }
  function profileToForm() {
    const f = $("#profileForm");
    Object.entries(state.profile).forEach(([k, v]) => {
      const el = f.elements[k]; if (!el) return;
      el.value = typeof v === "boolean" ? (v ? "yes" : "no") : v;
    });
    const anyOptional = OPTIONAL_FIELDS.some((k) => state.profile[k] !== undefined);
    $("#f_consent").checked = anyOptional; $("#optionalFields").hidden = !anyOptional;
  }
  function formToProfile() {
    const f = $("#profileForm"), out = {};
    $$("input[name], select[name]", f).forEach((el) => {
      if (OPTIONAL_FIELDS.includes(el.name) && !$("#f_consent").checked) return;   // consent first
      let v = el.value.trim();
      if (!v || v === "preferNot") return;
      if (BOOL_FIELDS.includes(el.name)) v = v === "yes";
      out[el.name] = v;
    });
    out.preferred_language = I18N.lang;
    return out;
  }
  async function saveProfile(e) {
    e.preventDefault();
    $$(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
    try {
      const res = await API.saveProfile(formToProfile());
      state.profile = res.profile;
      $("#profileMsg").textContent = t("profile.saved");
      location.hash = "#discover";
    } catch (err) {
      $("#profileMsg").textContent = t("profile.invalid");
      Object.keys((err.data && err.data.fields) || {}).forEach((k) => { const el = $(`[name="${k}"]`); if (el) el.classList.add("is-invalid"); });
    }
  }
  async function clearProfile() {
    await API.clearProfile().catch(() => {});
    state.profile = {}; state.docs.clear(); saveDocs(); state.saved.clear(); state.draftId = null;
    $("#profileForm").reset(); $("#f_state").value = "Kerala"; $("#optionalFields").hidden = true;
    $("#profileMsg").textContent = t("profile.cleared");
  }

  /* ---------- badges ---------- */
  function freshBadge(s) {
    const f = s.freshness || {};
    if (f.level === "test" && s.is_test) return "";   // the separate "Fictional test record" badge already says this
    const cls = { green: "b-green", amber: "b-amber", red: "b-red", test: "b-test" }[f.level] || "b-red";
    return `<span class="badge-j ${cls}">${esc(I18N.lang === "ml" ? f.label_ml : f.label_en)}</span>`;
  }

  /* ---------- discover: best for you ---------- */
  async function renderBest() {
    const box = $("#bestList");
    if (!hasProfile()) { box.innerHTML = `<p class="muted">${esc(t("best.needProfile"))} <a href="#profile">${esc(t("nav.profile"))}</a></p>`; return; }
    box.innerHTML = "…";
    try {
      const out = await API.best(state.profile, [...state.docs], state.preset);
      const items = out.results.map((r) => `
        <div class="best-item">
          <div class="best-rank">#${r.rank}</div>
          <div>
            <a href="#benefitiq" class="fw-semibold" data-open="${esc(r.scheme_id)}">${esc(I18N.lang === "ml" ? r.name_ml : r.name_en)}</a>
            ${r.is_test ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : ""}
            <span class="badge-j ${r.eligibility_status === "potentially_eligible" ? "b-green" : "b-amber"}">${esc(t("status." + r.eligibility_status))}</span>
            <div class="small">${(I18N.lang === "ml" ? r.strengths_ml : r.strengths_en).map((s) => `✓ ${esc(s)}`).join(" · ")}</div>
            ${(I18N.lang === "ml" ? r.weaknesses_ml : r.weaknesses_en).length ? `<div class="small muted">${(I18N.lang === "ml" ? r.weaknesses_ml : r.weaknesses_en).map((s) => `• ${esc(s)}`).join(" ")}</div>` : ""}
            ${r.fields_needed.length ? `<div class="small">${esc(t("best.fieldsNeeded"))}: ${r.fields_needed.map((f) => esc(fieldLabel(f))).join(", ")}</div>` : ""}
            ${(I18N.lang === "ml" ? r.warnings_ml : r.warnings_en).filter((w) => !w.startsWith(t("status." + r.eligibility_status))).map((w) => `<div class="small text-danger">⚠ ${esc(w)}</div>`).join("")}
          </div>
          <div class="best-score"><span class="small muted">${esc(t("best.score"))}</span><div>${Math.round(r.score * 100)}</div>
            <div class="bar" aria-hidden="true"><i style="width:${Math.round(r.score * 100)}%"></i></div></div>
        </div>`).join("");
      const excluded = out.excluded.length ? `<details class="mt-2"><summary>${esc(t("best.excluded"))} (${out.excluded.length})</summary>
        <ul class="small">${out.excluded.map((e) => `<li><strong>${esc(e.name_en)}</strong>: ${esc((I18N.lang === "ml" ? e.why_ml : e.why_en).join(" "))}</li>`).join("")}</ul></details>` : "";
      const dropped = out.criteria_dropped.includes("benefit") ? `<p class="small muted">${esc(t("best.dropped"))}</p>` : "";
      box.innerHTML = (items || `<p class="muted">${esc(I18N.lang === "ml" ? out.note_ml : out.note_en)}</p>`) + dropped + excluded;
    } catch (err) { box.innerHTML = `<p class="text-danger">${esc(err.message)}</p>`; }
  }

  /* ---------- discover: all schemes ---------- */
  async function renderSchemes() {
    const list = $("#schemeList");
    try {
      const { schemes } = await API.schemes($("#q").value, $("#catFilter").value, $("#verifiedOnly").checked);
      const saved = await API.saved().catch(() => ({ saved: [] }));
      state.saved = new Set(saved.saved);
      if (!schemes.length) { list.innerHTML = `<p class="muted">${esc(t("discover.none"))}</p>`; return; }
      list.innerHTML = schemes.map((s) => `
        <div class="col-md-6 col-xl-4"><article class="card-j scheme-card">
          <div>${freshBadge(s)}${s.is_test ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : ""}</div>
          <h4>${esc(L(s, "name"))}</h4>
          <p class="small muted mb-1">${esc(s.department)}</p>
          <p class="small">${esc(L(s, "description"))}</p>
          ${s.eligibility_rules.length ? `<p class="small mb-1 fw-semibold">${esc(t("card.conditions"))}</p>
            <ul class="small">${s.eligibility_rules.slice(0, 3).map((r) => `<li>${esc(L(r, "description"))}</li>`).join("")}</ul>` : ""}
          <p class="small"><strong>${esc(t("card.documents"))}:</strong> ${esc(s.required_documents.map((d) => L(d, "name")).join(", ") || t("biq.verify"))}</p>
          <div class="actions">
            <button class="btn btn-sm btn-primary-j" data-open="${esc(s.id)}">${esc(t("card.check"))}</button>
            <button class="btn btn-sm btn-outline-j" data-detail="${esc(s.id)}">${esc(t("card.details"))}</button>
            <button class="btn btn-sm btn-outline-secondary" data-save="${esc(s.id)}" aria-pressed="${state.saved.has(s.id)}">${esc(state.saved.has(s.id) ? t("card.saved") : t("card.save"))}</button>
            ${s.official_url ? `<a class="btn btn-sm btn-link" href="${esc(s.official_url)}" target="_blank" rel="noopener">${esc(t("card.source"))} ↗</a>`
                             : `<span class="small muted">${esc(t("card.noSource"))}</span>`}
          </div></article></div>`).join("");
    } catch (err) { list.innerHTML = `<p class="text-danger">${esc(err.message)}</p>`; }
  }

  async function showDetail(id) {
    const s = await API.scheme(id);
    $("#detailTitle").textContent = L(s, "name");
    const src = (s.sources || []).map((x) => x.url ? `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></li>` : `<li>${esc(x.title)}</li>`).join("");
    $("#detailBody").innerHTML = `
      <p>${freshBadge(s)}</p><p>${esc(L(s, "description"))}</p>
      <dl class="row">
        <dt class="col-sm-4">${esc(t("detail.department"))}</dt><dd class="col-sm-8">${esc(s.department)}</dd>
        <dt class="col-sm-4">${esc(t("detail.benefits"))}</dt><dd class="col-sm-8">${esc(s.benefits)}</dd>
        <dt class="col-sm-4">${esc(t("detail.method"))}</dt><dd class="col-sm-8">${esc(s.application_method)}</dd>
        <dt class="col-sm-4">${esc(t("detail.deadline"))}</dt><dd class="col-sm-8">${esc(s.deadline || t("biq.verify"))}</dd>
      </dl>
      <h3 class="h6">${esc(t("card.conditions"))}</h3><ul>${s.eligibility_rules.map((r) => `<li>${esc(L(r, "description"))}${r.verified ? "" : ` <span class="badge-j b-amber">${esc(t("biq.verify"))}</span>`}</li>`).join("") || `<li>${esc(t("biq.verify"))}</li>`}</ul>
      <h3 class="h6">${esc(t("card.documents"))}</h3><ul>${s.required_documents.map((d) => `<li>${esc(L(d, "name"))}</li>`).join("") || `<li>${esc(t("docs.noList"))}</li>`}</ul>
      ${src ? `<h3 class="h6">${esc(t("chat.sources"))}</h3><ul class="small">${src}</ul>` : ""}
      ${s.verification_note ? `<p class="small muted">${esc(s.verification_note)}</p>` : ""}`;
    bootstrap.Modal.getOrCreateInstance($("#detailModal")).show();
  }

  /* ---------- BenefitIQ ---------- */
  async function openScheme(id) {
    state.scheme = await API.scheme(id);
    state.draftId = null;
    location.hash = "#benefitiq";
    renderBenefitIQ();
  }
  async function renderBenefitIQ() {
    const s = state.scheme;
    $("#biqEmpty").hidden = !!s; $("#biqBody").hidden = !s;
    if (!s) return;
    $("#biqScheme").textContent = L(s, "name");
    $("#biqBadges").innerHTML = freshBadge(s) + (s.is_test ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : "");
    renderChecklist();
    await Promise.all([renderEligibility(), renderReadiness()]);
  }
  async function renderEligibility() {
    const box = $("#eligBox");
    if (!hasProfile()) { box.innerHTML = `<p>${esc(t("biq.needProfile"))} <a href="#profile">${esc(t("nav.profile"))}</a></p>`; return; }
    const r = await API.eligibility(state.profile, state.scheme.id);
    const list = (items, cls) => items.length ? `<ul class="cond ${cls}">${items.map((i) => `<li>${esc(L(i, "description"))}${cls === "unk" ? ` <em class="small">${esc(L(i, "ask"))}</em>` : ""}</li>`).join("")}</ul>` : "";
    box.innerHTML = `
      <div class="status st-${r.status}">${esc(t("status." + r.status))}</div>
      ${r.met.length ? `<h4 class="h6">${esc(t("biq.met"))}</h4>${list(r.met, "met")}` : ""}
      ${r.not_met.length ? `<h4 class="h6">${esc(t("biq.notMet"))}</h4>${list(r.not_met, "not")}` : ""}
      ${r.unknown.length ? `<h4 class="h6">${esc(t("biq.unknown"))}</h4>${list(r.unknown, "unk")} <a href="#profile">${esc(t("nav.profile"))} →</a>` : ""}
      ${r.counterfactuals.length ? `<div class="whatif mt-2"><strong>${esc(t("biq.whatIf"))}</strong><ul class="mb-0">${r.counterfactuals.map((c) => `<li>${esc(L(c, "text"))}</li>`).join("")}</ul></div>` : ""}
      <p class="small muted mt-2 mb-0">${esc(L(r, "note"))}</p>`;
  }
  async function renderReadiness() {
    const r = await API.readiness(state.scheme.id, [...state.docs], state.profile);
    const name = (i) => (I18N.lang === "ml" ? i.name_ml : i.name_en);
    $("#readyBox").innerHTML = `
      <div class="ring" style="--p:${r.score}" role="img" aria-label="${r.score}%"><span>${r.score}%</span></div>
      <p class="small text-center muted">${esc(L(r, "note"))}</p>
      ${r.completed.length ? `<h4 class="h6">${esc(t("biq.completed"))}</h4><ul class="cond met">${r.completed.map((i) => `<li>${esc(i.kind === "field" ? fieldLabel(i.id) : name(i))}</li>`).join("")}</ul>` : ""}
      ${r.missing.length ? `<h4 class="h6">${esc(t("biq.mandatoryMissing"))}</h4><ul class="cond not">${r.missing.map((i) => `<li>${esc(i.kind === "field" ? fieldLabel(i.id) : name(i))}</li>`).join("")}</ul>` : ""}
      ${r.needs_verification.length ? `<h4 class="h6">${esc(t("biq.verify"))}</h4><ul class="cond unk">${r.needs_verification.map((i) => `<li>${esc(name(i))}</li>`).join("")}</ul>` : ""}
      <h4 class="h6">${esc(t("biq.next"))}</h4><ul>${(I18N.lang === "ml" ? r.next_steps_ml : r.next_steps_en).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`;
  }
  function renderChecklist() {
    const docs = state.scheme.required_documents || [];
    $("#docChecklist").innerHTML = docs.length ? docs.map((d) => `
      <label class="doc-row"><input type="checkbox" data-doc="${esc(d.id)}" ${state.docs.has(d.id) ? "checked" : ""}>
      <span>${esc(L(d, "name"))} <span class="small muted">(${esc(t(d.mandatory ? "docs.mandatory" : "docs.optional"))})</span></span></label>`).join("") +
      `<p class="small muted mt-2 mb-0">${esc(t("docs.haveHint"))}</p>`
      : `<p class="text-warning-emphasis">${esc(t("docs.noList"))}</p>`;
  }

  /* ---------- OCR + consistency check ---------- */
  async function readDocument(e) {
    e.preventDefault();
    const file = $("#ocrFile").files[0]; const out = $("#ocrResult");
    if (!file) { out.innerHTML = `<p>${esc(t("docs.manual"))}</p>`; return; }
    out.textContent = t("docs.reading");
    try {
      const r = await API.analyzeDoc(file, I18N.lang, { name: state.profile.name, annual_income: state.profile.annual_income });
      state.ocrFields = r.fields;
      renderOcr(r);
    } catch (err) { out.innerHTML = `<div class="mismatch">${esc(err.message)}</div><p>${esc(t("docs.manual"))}</p>`; }
  }
  function renderOcr(r) {
    const f = state.ocrFields || {};
    const rows = [["name", "docs.name"], ["annual_income", "docs.income"], ["village", "docs.village"], ["taluk", "docs.taluk"], ["issue_date", "docs.date"]];
    $("#ocrResult").innerHTML = `
      <p class="small">${esc(r.message || "")} ${r.confidence ? `· ${esc(t("docs.confidence"))}: ${r.confidence}%` : ""}</p>
      <form id="ocrEdit"><p class="fw-semibold small">${esc(t("docs.review"))}</p>
        ${rows.map(([k, label]) => `<div class="mb-2"><label class="form-label small" for="ocr_${k}">${esc(t(label))}</label>
          <input class="form-control form-control-sm" id="ocr_${k}" name="${k}" value="${esc(f[k] ?? "")}"></div>`).join("")}
        <div class="d-flex flex-wrap gap-2"><button class="btn btn-sm btn-outline-j" type="submit">${esc(t("docs.recheck"))}</button>
        <button class="btn btn-sm btn-primary-j" type="button" id="useOcr">${esc(t("docs.useValues"))}</button></div>
      </form>
      <div id="mismatchBox" class="mt-2"></div>
      <p class="small muted mt-2">${esc(r.note || "")}</p>`;
    renderMismatches(r.mismatches || []);
  }
  function renderMismatches(list) {
    $("#mismatchBox").innerHTML = list.length
      ? list.map((m) => `<div class="mismatch">⚠ ${esc(m.message)}${m.document_value != null ? ` (${esc(m.document_value)} ≠ ${esc(m.entered_value)})` : ""}</div>`).join("")
      : `<div class="ok-box">✓ ${esc(t("docs.noMismatch"))}</div>`;
  }
  function ocrFormValues() {
    const out = {};
    $$("#ocrEdit input").forEach((i) => { const v = i.value.trim(); out[i.name] = v === "" ? null : (i.name === "annual_income" ? Number(v.replace(/[^\d]/g, "")) || null : v); });
    return out;
  }
  async function recheckOcr(e) {
    e.preventDefault();
    state.ocrFields = ocrFormValues();
    const r = await API.consistency(state.ocrFields, { name: state.profile.name, annual_income: state.profile.annual_income }, I18N.lang);
    renderMismatches(r.mismatches);
  }
  async function useOcrValues() {
    const v = ocrFormValues();
    const next = { ...state.profile };
    if (v.name) next.name = v.name;
    if (v.annual_income) next.annual_income = v.annual_income;
    const res = await API.saveProfile(next);
    state.profile = res.profile; profileToForm();
    state.docs.add("income_cert"); saveDocs();
    toast(t("toast.saved"));
    renderBenefitIQ();
  }

  /* ---------- application draft ---------- */
  function draftFieldList(s) {
    const base = ["name", "state", "district", "education_level", "course", "institution_name", "annual_income"];
    return [...new Set(base.concat((s.eligibility_rules || []).map((r) => r.field)))];
  }
  function renderApply() {
    const s = state.scheme;
    $("#applyEmpty").hidden = !!s; $("#applyBody").hidden = !s;
    if (!s) return;
    $("#applyMethod").textContent = s.application_method || t("biq.verify");
    $("#applyLinks").innerHTML = (s.official_url ? `<a class="btn btn-outline-j" href="${esc(s.official_url)}" target="_blank" rel="noopener">${esc(t("apply.official"))} ↗</a>` : `<span class="muted">${esc(t("card.noSource"))}</span>`)
      + `<button type="button" class="btn btn-outline-j" id="packetBtn">${esc(t("apply.packet"))}</button>`;
    $("#draftFields").innerHTML = draftFieldList(s).map((f) => {
      const v = state.profile[f];
      const shown = typeof v === "boolean" ? (v ? "yes" : "no") : (v ?? "");
      return `<div class="col-md-6"><label class="form-label" for="d_${f}">${esc(fieldLabel(f))}</label>
        <input class="form-control ${shown === "" ? "field-missing" : ""}" id="d_${f}" name="${esc(f)}" value="${esc(shown)}"
        ${shown === "" ? `aria-describedby="m_${f}"` : ""}>${shown === "" ? `<div class="small text-danger" id="m_${f}">${esc(t("apply.missing"))}</div>` : ""}</div>`;
    }).join("");
    $("#draftBtn").textContent = t(state.draftId ? "apply.updateDraft" : "apply.makeDraft");
  }
  async function saveDraft(e) {
    e.preventDefault();
    const edits = {};
    $$("#draftFields input").forEach((i) => {
      const v = i.value.trim(); if (!v) return;
      edits[i.name] = BOOL_FIELDS.includes(i.name) ? ["yes", "true", "അതെ"].includes(v.toLowerCase()) : v;
    });
    try {
      const d = await API.draft({ scheme_id: state.scheme.id, profile: state.profile, provided_items: [...state.docs], edits, draft_id: state.draftId });
      state.draftId = d.draft_id;
      $("#draftMissing").innerHTML = d.missing_items.length
        ? `<div class="mismatch"><strong>${esc(t("apply.missing"))}:</strong> ${d.missing_items.map((m) => esc(PROFILE_LABEL[m] ? fieldLabel(m) : m.replace(/_/g, " "))).join(", ")}</div>`
        : `<div class="ok-box">✓ 100%</div>`;
      $("#confirmBox").hidden = false; $("#demoBanner").hidden = true;
      $("#confirmCheck").checked = false; $("#confirmBtn").disabled = true;
      $("#draftBtn").textContent = t("apply.updateDraft");
    } catch (err) { $("#draftMissing").innerHTML = `<div class="mismatch">${esc(err.message)}</div>`; }
  }
  async function confirmDraft() {
    const r = await API.confirm(state.draftId);
    $("#demoBanner").textContent = L(r, "message"); $("#demoBanner").hidden = false;
    $("#confirmBtn").disabled = true;
  }
  async function downloadPacket() {
    const fields = draftFieldList(state.scheme).filter((f) => state.profile[f] === undefined);
    const blob = await API.packet(state.scheme.id, [...state.docs], fields);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `janaseva_packet_${state.scheme.id}.pdf`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  /* ---------- document verification with the panchayat ---------- */
  const STATUS_BADGE = { submitted: "b-amber", queued_for_review: "b-amber", under_review: "b-amber", info_required: "b-red",
                         verified: "b-green", unsuccessful: "b-red", referred: "b-test", cancelled: "b-test" };
  let verifyLink = null;
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString(I18N.lang === "ml" ? "ml-IN" : "en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

  async function renderVerify() {
    try { verifyLink = await API.verifyLink(); } catch (e) { verifyLink = { linked: false }; }
    const box = $("#vLinkBox");
    if (!verifyLink.linked) {
      box.innerHTML = `<h3 class="h5">${esc(t("verify.linkTitle"))}</h3><p class="small muted">${esc(t("verify.linkHint"))}</p>
        <form id="vLinkForm" class="row g-2" novalidate>
          <div class="col-md-5"><label class="form-label" for="vHh">${esc(t("verify.householdRef"))}</label><input class="form-control" id="vHh" autocomplete="off" required></div>
          <div class="col-md-4"><label class="form-label" for="vCode">${esc(t("verify.linkCode"))}</label><input class="form-control" id="vCode" autocomplete="off" required></div>
          <div class="col-md-3 d-flex align-items-end"><button class="btn btn-primary-j w-100">${esc(t("verify.link"))}</button></div>
          <div class="col-12 small" id="vLinkMsg" role="alert"></div>
          <div class="col-12 small muted">${esc(t("verify.demoHint"))}</div></form>`;
      $("#vLinked").hidden = true;
    } else {
      box.innerHTML = `<div class="d-flex flex-wrap justify-content-between gap-2"><div><span class="muted small">${esc(t("verify.linkedTo"))}</span>
        <div class="fw-semibold">${esc(verifyLink.household_ref)} · ${esc(verifyLink.panchayat)} · ${esc(verifyLink.ward)}</div>
        ${verifyLink.is_demo ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : ""}</div>
        <button class="btn btn-sm btn-outline-secondary align-self-start" id="vUnlink">${esc(t("verify.unlink"))}</button></div>
        <p class="small muted mb-0 mt-2">${esc(t("verify.identityNote"))}</p>`;
      $("#vLinked").hidden = false;
      await fillVerifyForm();
      await renderVerifyRequests();
    }
    renderVerifyNotifications();
  }

  async function fillVerifyForm() {
    const cats = (await API.verifyCategories()).categories;
    const sel = $("#vCat"), current = sel.value;
    sel.innerHTML = `<option value="">${esc(t("verify.chooseCategory"))}</option>` +
      cats.map((c) => `<option value="${esc(c.code)}" data-check="${c.panchayat_can_check ? 1 : 0}">${esc(I18N.lang === "ml" ? c.name_ml : c.name_en)}</option>`).join("");
    sel.value = current;
    $("#vMember").innerHTML = verifyLink.members.map((m) => `<option value="${esc(m.member_ref)}">${esc(m.name)}</option>`).join("");
    const { schemes } = await API.schemes();
    $("#vScheme").innerHTML = `<option value="">${esc(t("verify.none"))}</option>` + schemes.map((s) => `<option value="${esc(s.id)}">${esc(L(s, "name"))}</option>`).join("");
    if (state.scheme) { $("#vScheme").value = state.scheme.id; if (!$("#vPurpose").value) $("#vPurpose").value = L(state.scheme, "name"); }
  }

  async function renderVerifyRequests() {
    const box = $("#vRequests");
    const { items } = await API.verifyList();
    if (!items.length) { box.innerHTML = `<p class="muted mb-0">${esc(t("verify.none.requests"))}</p>`; return; }
    box.innerHTML = items.map((r) => `
      <article class="border rounded-3 p-3 mb-3" aria-labelledby="vr${r.id}">
        <div class="d-flex flex-wrap justify-content-between gap-2">
          <div><h4 class="h6 mb-1" id="vr${r.id}"><span class="font-monospace">${esc(r.request_ref)}</span> · ${esc(L(r, "category"))}</h4>
            <div class="small muted">${esc(r.purpose)} · ${esc(t("verify.updated"))}: ${esc(fmt(r.updated_at))}</div></div>
          <span class="badge-j ${STATUS_BADGE[r.status]} align-self-start fs-6">${esc(L(r, "status"))}</span>
        </div>
        ${r.official_remarks ? `<div class="mt-2 ${r.status === "info_required" || r.status === "unsuccessful" ? "mismatch" : "ok-box"}"><strong>${esc(t("verify.remarks"))}:</strong> ${esc(r.official_remarks)}</div>` : ""}
        ${r.referral_authority ? `<div class="small mt-1"><strong>${esc(t("verify.referredTo"))}:</strong> ${esc(r.referral_authority)}</div>` : ""}
        <p class="small mt-2 mb-1"><strong>${esc(t("verify.next"))}:</strong> ${esc(L(r, "next_step"))}</p>
        ${r.status === "verified" ? `<div class="d-flex flex-wrap gap-2 mt-2"><button class="btn btn-sm btn-success" data-receipt="${r.id}" data-ref="${esc(r.request_ref)}">${esc(t("verify.receipt"))}</button>
          ${r.scheme_id ? `<button class="btn btn-sm btn-outline-j" data-open="${esc(r.scheme_id)}">${esc(t("verify.applyNow"))} →</button>` : ""}
          <span class="small muted align-self-center">${esc(t("verify.notApproval"))}</span></div>` : ""}
        ${r.can_resubmit ? `<form class="mt-2 row g-2" data-resubmit="${r.id}">
          <div class="col-md-5"><label class="form-label small" for="rf${r.id}">${esc(t("verify.resubmit"))}</label><input class="form-control form-control-sm" type="file" id="rf${r.id}" name="file" accept="application/pdf,image/png,image/jpeg" required></div>
          <div class="col-md-5"><label class="form-label small" for="rn${r.id}">${esc(t("verify.resubmitNote"))}</label><input class="form-control form-control-sm" id="rn${r.id}" name="note" maxlength="500"></div>
          <div class="col-md-2 d-flex align-items-end"><button class="btn btn-sm btn-primary-j w-100">${esc(t("verify.send"))}</button></div></form>` : ""}
        <details class="mt-2"><summary class="small">${esc(t("verify.history"))}</summary><ul class="small mb-0">
          ${r.history.map((h) => `<li>${esc(fmt(h.at))} - ${esc(I18N.lang === "ml" ? h.status_ml : h.status_en)}</li>`).join("")}</ul></details>
        ${r.can_cancel ? `<button class="btn btn-sm btn-link text-danger p-0 mt-1" data-cancel="${r.id}">${esc(t("verify.cancel"))}</button>` : ""}
        <p class="small muted mt-2 mb-0">${esc(L(r, "disclaimer"))}</p>
      </article>`).join("");
  }

  async function renderVerifyNotifications() {
    try {
      const n = await API.notifications();
      const badge = $("#verifyBadge"); badge.textContent = n.unread; badge.hidden = !n.unread;
      const unread = n.items.filter((i) => !i.is_read);
      $("#vNotifications").innerHTML = unread.length && !$("#verify").hidden ? `<div class="card-j" style="border-left:4px solid var(--accent)" role="status">
        <div class="d-flex justify-content-between"><strong>${esc(t("verify.notifications"))}</strong><button class="btn btn-sm btn-link p-0" id="vReadAll">${esc(t("verify.markRead"))}</button></div>
        <ul class="mb-0 small">${unread.slice(0, 5).map((i) => `<li><strong>${esc(I18N.lang === "ml" ? i.title_ml : i.title_en)}</strong> - ${esc(I18N.lang === "ml" ? i.body_ml : i.body_en)}</li>`).join("")}</ul></div>` : "";
    } catch (e) { /* notifications are optional */ }
  }

  async function linkHousehold(e) {
    e.preventDefault();
    try { await API.verifyLinkCreate($("#vHh").value, $("#vCode").value); renderVerify(); }
    catch (err) { $("#vLinkMsg").innerHTML = `<span class="text-danger">${esc(err.message)}</span>`; }
  }
  async function submitVerification(e) {
    e.preventDefault();
    const f = $("#vSubmitForm"), msg = $("#vSubmitMsg");
    msg.textContent = "…";
    try {
      const r = await API.verifySubmit(new FormData(f));
      msg.innerHTML = `<span class="text-success">✓ ${esc(t("verify.sent"))} <strong>${esc(r.request_ref)}</strong></span>`;
      f.reset(); fillVerifyForm(); renderVerifyRequests(); renderVerifyNotifications();
    } catch (err) { msg.innerHTML = `<span class="text-danger">${esc(err.message)}</span>`; }
  }
  async function downloadReceipt(id, ref) {
    const blob = await API.verifyReceipt(id);
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = `${ref}-verification-record.pdf`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  /* ---------- dashboard ---------- */
  async function renderDashboard() {
    const [saved, drafts] = await Promise.all([API.saved().catch(() => ({ saved: [] })), API.drafts().catch(() => ({ drafts: [] }))]);
    const schemes = await Promise.all(saved.saved.map((id) => API.scheme(id).catch(() => null)));
    $("#savedList").innerHTML = schemes.filter(Boolean).map((s) => `<p><a href="#benefitiq" data-open="${esc(s.id)}">${esc(L(s, "name"))}</a> ${freshBadge(s)}${s.is_test ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : ""}</p>`).join("") || `<p class="muted">${esc(t("dash.none"))}</p>`;
    $("#draftList").innerHTML = drafts.drafts.map((d) => `<div class="mb-2"><strong>${esc(I18N.lang === "ml" ? d.scheme_name_ml : d.scheme_name_en)}</strong>
      <span class="badge-j ${d.status === "draft" ? "b-amber" : "b-green"}">${esc(t("dash.status." + d.status))}</span>
      <span class="small">${d.readiness_score}% · ${esc(t("apply.missing"))}: ${d.missing_items.length}</span>
      <a href="#apply" class="small ms-2" data-open-draft="${esc(d.scheme_id)}" data-draft-id="${d.id}">${esc(t("dash.open"))}</a></div>`).join("") || `<p class="muted">${esc(t("dash.none"))}</p>`;
  }

  /* ---------- chat ---------- */
  const chatLog = () => $("#chatLog");
  function addMsg(html, who) {
    const div = document.createElement("div"); div.className = "msg " + who; div.innerHTML = html;
    chatLog().appendChild(div); chatLog().scrollTop = chatLog().scrollHeight; return div;
  }
  async function sendChat(text) {
    if (!text.trim()) return;
    addMsg(esc(text), "user");
    const wait = addMsg("…", "bot");
    try {
      const r = await API.chat(text, I18N.lang, state.profile, state.scheme ? state.scheme.id : undefined);
      wait.innerHTML = renderBot(r);
      if ($("#readAloud").checked) speak(r.explanation, r.lang);
    } catch (err) { wait.textContent = t("chat.error"); }
  }
  function renderBot(r) {
    const facts = (r.facts || []).slice(0, 3).map((f) => `
      <div class="facts"><h5>📋 ${esc(t("chat.facts"))}</h5>
        <strong>${esc(f.name)}</strong> ${f.is_test ? `<span class="badge-j b-test">${esc(t("card.test"))}</span>` : ""}
        <div>${esc(f.description)}</div>
        <div><em>${esc(t("card.conditions"))}:</em> ${esc(f.conditions.join("; "))}</div>
        <div><em>${esc(t("card.documents"))}:</em> ${esc(f.documents.join(", "))}</div>
        <div><em>${esc(t("detail.deadline"))}:</em> ${esc(f.deadline)}</div>
        <button class="btn btn-sm btn-link p-0" data-open="${esc(f.scheme_id)}">${esc(t("card.open"))} →</button>
      </div>`).join("");
    const sources = (r.sources || []).filter((s) => s.url).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>`).join(" · ");
    const follow = r.follow_up ? `<form class="followup mt-2" data-fields="${esc(r.follow_up.fields.join(","))}"><strong>${esc(t("chat.followUp"))}</strong>
      ${r.follow_up.fields.map((f) => f === "education_level"
        ? `<select class="form-select form-select-sm mt-1" name="${f}" aria-label="${esc(fieldLabel(f))}"><option value="">${esc(fieldLabel(f))}</option>${["SSLC", "HSE", "DIPLOMA", "UG", "PG"].map((o) => `<option value="${o}">${esc(t("opt." + o))}</option>`).join("")}</select>`
        : `<input class="form-control form-control-sm mt-1" name="${f}" placeholder="${esc(fieldLabel(f))}" aria-label="${esc(fieldLabel(f))}" value="${f === "state" ? "Kerala" : ""}">`).join("")}
      <button class="btn btn-sm btn-primary-j mt-2">${esc(t("chat.useAnswers"))}</button></form>` : "";
    return `<div class="explain"><h5>💬 ${esc(t("chat.explain"))}</h5>${esc(r.explanation)}</div>${facts}${follow}
      ${sources ? `<div class="small mt-1">${esc(t("chat.sources"))}: ${sources}</div>` : ""}`;
  }
  async function answerFollowUp(form) {
    const add = {};
    $$("input, select", form).forEach((el) => { if (el.value.trim()) add[el.name] = el.value.trim(); });
    try {
      const res = await API.saveProfile({ ...state.profile, ...add });
      state.profile = res.profile; profileToForm();
      form.remove();
      sendChat(I18N.lang === "ml" ? "എനിക്ക് ലഭിക്കാവുന്ന സ്കോളർഷിപ്പുകൾ ഏതൊക്കെയാണ്?" : "Which scholarships can I get?");
    } catch (err) { addMsg(esc(t("profile.invalid")), "bot"); }
  }

  /* voice input + read aloud (browser built-in; works best in Chrome/Edge) */
  let recog = null;
  function toggleMic() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { addMsg(esc(t("chat.noVoice")), "bot"); return; }
    if (recog) { recog.stop(); return; }
    recog = new SR(); recog.lang = I18N.lang === "ml" ? "ml-IN" : "en-IN"; recog.interimResults = false;
    $("#micBtn").classList.add("mic-on"); $("#chatText").placeholder = t("chat.listening");
    recog.onresult = (e) => { $("#chatText").value = e.results[0][0].transcript; sendChat($("#chatText").value); $("#chatText").value = ""; };
    recog.onend = () => { recog = null; $("#micBtn").classList.remove("mic-on"); $("#chatText").placeholder = t("chat.placeholder"); };
    recog.onerror = recog.onend;
    recog.start();
  }
  function speak(text, lang) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text); u.lang = lang === "ml" ? "ml-IN" : "en-IN";
    const voice = speechSynthesis.getVoices().find((v) => v.lang && v.lang.toLowerCase().startsWith(u.lang.slice(0, 2)));
    if (voice) u.voice = voice;
    speechSynthesis.speak(u);
  }

  /* ---------- events ---------- */
  function bind() {
    window.addEventListener("hashchange", route);
    $$(".btn-lang").forEach((b) => b.addEventListener("click", () => I18N.set(b.dataset.lang)));
    $("#textSizeBtn").addEventListener("click", () => {
      document.documentElement.classList.toggle("large");
      store.set("janaseva_large", document.documentElement.classList.contains("large"));
    });
    const openChat = () => bootstrap.Offcanvas.getOrCreateInstance($("#chatPanel")).show();
    $("#openChat").addEventListener("click", openChat);
    $("#profileForm").addEventListener("submit", saveProfile);
    $("#clearProfile").addEventListener("click", clearProfile);
    $("#f_consent").addEventListener("change", (e) => { $("#optionalFields").hidden = !e.target.checked; });
    $("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); renderSchemes(); });
    $("#verifiedOnly").addEventListener("change", renderSchemes);
    $("#catFilter").addEventListener("change", renderSchemes);
    $$("#presetGroup button").forEach((b) => b.addEventListener("click", () => {
      state.preset = b.dataset.preset;
      $$("#presetGroup button").forEach((x) => x.classList.toggle("active", x === b));
      renderBest();
    }));
    $("#ocrForm").addEventListener("submit", readDocument);
    $("#draftForm").addEventListener("submit", saveDraft);
    $("#confirmCheck").addEventListener("change", (e) => { $("#confirmBtn").disabled = !e.target.checked; });
    $("#confirmBtn").addEventListener("click", confirmDraft);
    $("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#chatText").value; $("#chatText").value = ""; sendChat(v); });
    $("#micBtn").addEventListener("click", toggleMic);

    // delegated clicks for content rendered later
    document.addEventListener("click", async (e) => {
      const el = e.target.closest("[data-open],[data-detail],[data-save],[data-action],[data-open-draft],#packetBtn,#useOcr");
      if (!el) return;
      if (el.dataset.action === "open-chat") { openChat(); return; }
      if (el.id === "packetBtn") { downloadPacket(); return; }
      if (el.id === "useOcr") { useOcrValues(); return; }
      e.preventDefault();
      if (el.dataset.open) {
        const panel = bootstrap.Offcanvas.getInstance($("#chatPanel")); if (panel) panel.hide();
        openScheme(el.dataset.open);
      }
      if (el.dataset.detail) showDetail(el.dataset.detail);
      if (el.dataset.openDraft) { state.scheme = await API.scheme(el.dataset.openDraft); state.draftId = Number(el.dataset.draftId); location.hash = "#apply"; renderApply(); }
      if (el.dataset.save) {
        const r = await API.toggleSaved(el.dataset.save);
        el.textContent = t(r.saved ? "card.saved" : "card.save"); el.setAttribute("aria-pressed", r.saved);
      }
    });
    document.addEventListener("change", (e) => {
      if (e.target.matches("[data-doc]")) {
        e.target.checked ? state.docs.add(e.target.dataset.doc) : state.docs.delete(e.target.dataset.doc);
        saveDocs(); renderReadiness();
      }
    });
    document.addEventListener("submit", (e) => {
      if (e.target.id === "ocrEdit") recheckOcr(e);
      if (e.target.classList.contains("followup")) { e.preventDefault(); answerFollowUp(e.target); }
    });
    // document verification events (content is rendered dynamically)
    document.addEventListener("submit", async (e) => {
      if (e.target.id === "vLinkForm") linkHousehold(e);
      if (e.target.id === "vSubmitForm") submitVerification(e);
      if (e.target.dataset.resubmit) {
        e.preventDefault();
        try { await API.verifyResubmit(e.target.dataset.resubmit, new FormData(e.target)); toast(t("verify.sent")); renderVerifyRequests(); renderVerifyNotifications(); }
        catch (err) { toast(err.message); }
      }
    });
    document.addEventListener("click", async (e) => {
      const el = e.target.closest("#vUnlink,#vReadAll,[data-cancel],[data-receipt]");
      if (!el) return;
      e.preventDefault();
      try {
        if (el.id === "vUnlink") { await API.verifyUnlink(); renderVerify(); }
        if (el.id === "vReadAll") { await API.notificationsRead(); renderVerifyNotifications(); }
        if (el.dataset.cancel && confirm(t("verify.cancel") + "?")) { await API.verifyCancel(el.dataset.cancel); renderVerifyRequests(); }
        if (el.dataset.receipt) await downloadReceipt(el.dataset.receipt, el.dataset.ref);
      } catch (err) { toast(err.message); }
    });
    document.addEventListener("change", (e) => {
      if (e.target.id === "vCat") {
        const opt = e.target.selectedOptions[0];
        $("#vCatNote").textContent = opt && opt.dataset.check === "0" ? t("verify.cannotCheck") : "";
      }
    });
    // light polling so status changes made by the panchayat appear without reloading
    setInterval(() => { if (!document.hidden) { renderVerifyNotifications(); if (!$("#verify").hidden && verifyLink && verifyLink.linked) renderVerifyRequests(); } }, 30000);

    document.addEventListener("langchange", () => {
      fillSelects(); profileToForm(); renderHome(); route();
      if (state.scheme && !$("#benefitiq").hidden) renderBenefitIQ();
    });
  }

  async function start() {
    await I18N.load();
    if (store.get("janaseva_large", false)) document.documentElement.classList.add("large");
    fillSelects(); I18N.apply(); renderHome(); bind();
    try { state.profile = (await API.getProfile()).profile || {}; } catch (e) { state.profile = {}; }
    profileToForm();
    route();
    renderVerifyNotifications();
  }
  start();
})();
