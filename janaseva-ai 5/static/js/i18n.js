/* English / Malayalam switching (owned by P2). Labels live in static/i18n/en.json and ml.json. */
const I18N = (() => {
  let lang = "en";
  const dict = { en: {}, ml: {} };
  try { lang = localStorage.getItem("janaseva_lang") || "en"; } catch (e) { /* default */ }

  async function load() {
    const [en, ml] = await Promise.all(["en", "ml"].map((l) => fetch(`/static/i18n/${l}.json`).then((r) => r.json())));
    dict.en = en; dict.ml = ml;
  }
  function t(key) { return (dict[lang] && dict[lang][key]) || dict.en[key] || key; }
  function apply(root = document) {
    document.documentElement.lang = lang;
    root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    document.querySelectorAll(".btn-lang").forEach((b) => b.classList.toggle("active", b.dataset.lang === lang));
  }
  function set(l) {
    lang = l === "ml" ? "ml" : "en";
    try { localStorage.setItem("janaseva_lang", lang); } catch (e) { /* ignore */ }
    apply();
    document.dispatchEvent(new CustomEvent("langchange"));
  }
  return { load, t, apply, set, get lang() { return lang; } };
})();
window.I18N = I18N;
