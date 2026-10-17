/* API helper (owned by P2). Every call goes to the Flask server; no secrets live here. */
const API = (() => {
  function clientId() {
    let id = null;
    try { id = localStorage.getItem("janaseva_client_id"); } catch (e) { /* storage blocked */ }
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
      try { localStorage.setItem("janaseva_client_id", id); } catch (e) { /* keep in memory */ }
    }
    return id;
  }
  const CID = clientId();

  async function call(method, url, body, isForm) {
    const opts = { method, headers: { "X-Client-Id": CID } };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    }
    let res;
    try { res = await fetch(url, opts); }
    catch (e) { throw new Error(window.I18N ? I18N.t("error.network") : "Network error"); }
    const type = res.headers.get("Content-Type") || "";
    if (type.includes("application/pdf")) {
      if (!res.ok) throw new Error("PDF error");
      return res.blob();
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.error || "Error"); err.data = data; throw err; }
    return data;
  }

  return {
    clientId: CID,
    schemes: (q = "", category = "", verifiedOnly = false) =>
      call("GET", `/api/schemes?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}${verifiedOnly ? "&verified_only=1" : ""}`),
    scheme: (id) => call("GET", `/api/schemes/${encodeURIComponent(id)}`),
    schemeUpdates: () => call("GET", "/api/schemes/updates"),
    getProfile: () => call("GET", "/api/profile"),
    saveProfile: (profile) => call("POST", "/api/profile", { profile }),
    clearProfile: () => call("DELETE", "/api/profile"),
    eligibility: (profile, scheme_id) => call("POST", "/api/eligibility/check", { profile, scheme_id }),
    best: (profile, provided_docs, preset) => call("POST", "/api/recommend/best", { profile, provided_docs, preset }),
    readiness: (scheme_id, provided_items, profile) => call("POST", "/api/application/readiness", { scheme_id, provided_items, profile }),
    draft: (body) => call("POST", "/api/application/draft", body),
    confirm: (draft_id) => call("POST", "/api/application/confirm", { draft_id, confirmed: true }),
    getDraft: (id) => call("GET", `/api/application/${id}`),
    checkApp: (id, profile) => call("POST", `/api/application/${id}/check`, { profile }),
    appPdf: (id, kind, download) => call("GET", `/api/application/${id}/pdf?kind=${kind}${download ? "&download=1" : ""}`),
    submitApp: (id, profile) => call("POST", `/api/application/${id}/submit`, { profile, confirmed: true }),
    drafts: () => call("GET", "/api/application"),
    saved: () => call("GET", "/api/saved"),
    toggleSaved: (scheme_id) => call("POST", "/api/saved", { scheme_id }),
    chat: (message, lang, profile, scheme_id) => call("POST", "/api/chat", { message, lang, profile, scheme_id }),
    analyzeDoc: (file, lang, reference) => {
      const fd = new FormData();
      fd.append("file", file); fd.append("doc_type", "income_cert"); fd.append("lang", lang);
      fd.append("reference", JSON.stringify(reference || {}));
      return call("POST", "/api/documents/analyze", fd, true);
    },
    consistency: (extracted, reference, lang) => call("POST", "/api/documents/consistency", { extracted, reference, lang }),
    packet: (scheme_id, provided_items, missing_fields) => call("POST", "/api/packet", { scheme_id, provided_items, missing_fields }),
    // document verification with the panchayat
    verifyLink: () => call("GET", "/api/citizen/link"),
    verifyLinkCreate: (household_ref, link_code) => call("POST", "/api/citizen/link", { household_ref, link_code }),
    verifyUnlink: () => call("DELETE", "/api/citizen/link"),
    verifyCategories: () => call("GET", "/api/citizen/document-categories"),
    verifySubmit: (formData) => call("POST", "/api/citizen/verification-requests", formData, true),
    verifyList: () => call("GET", "/api/citizen/verification-requests"),
    verifyResubmit: (id, formData) => call("POST", `/api/citizen/verification-requests/${id}/resubmit`, formData, true),
    verifyCancel: (id) => call("POST", `/api/citizen/verification-requests/${id}/cancel`),
    verifyReceipt: (id) => call("GET", `/api/citizen/verification-requests/${id}/receipt`),
    notifications: () => call("GET", "/api/citizen/notifications"),
    notificationsRead: () => call("POST", "/api/citizen/notifications/read-all"),
  };
})();
