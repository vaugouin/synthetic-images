// Labo images synthétiques (SYNTHETIC-IMAGES-020). Vanilla JS, relative URLs only: the page is
// served under /synthetic-review/ behind NGINX, and every call stays under that prefix.
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  config: null,
  entity: null,       // {id_item, name, ...}
  prep: null,         // /api/preview response
  desc: null,         // /api/describe response
  lightboxImage: null,
  pollTimer: null,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function money(value) {
  const n = Number(value || 0);
  return n < 0.01 && n > 0 ? n.toFixed(4) + " $" : n.toFixed(3) + " $";
}

function toast(message, isError) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, isError ? 7000 : 3000);
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const rsp = await fetch("api/" + path, options);
  let data = null;
  try { data = await rsp.json(); } catch (err) { data = null; }
  if (!rsp.ok) {
    throw new Error((data && data.detail) ? data.detail : ("HTTP " + rsp.status));
  }
  return data;
}

function showBudget(data) {
  if (!data || data.spent_today === undefined) return;
  $("budget").textContent = "Aujourd'hui : " + money(data.spent_today) + " sur " +
    money(data.daily_budget) + (state.config && state.config.dry_run ? " · mode simulation" : "");
}

function currentClass() { return $("classSelect").value; }

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
async function loadConfig() {
  const cfg = await api("config");
  state.config = cfg;
  $("classSelect").innerHTML = cfg.classes.map((c) =>
    `<option value="${esc(c.id)}">${esc(c.id)}${c.hint ? " (" + esc(c.hint) + ")" : ""}</option>`).join("");
  $("t2tSelect").innerHTML = cfg.t2t.map((m) =>
    `<option value="${esc(m.id)}"${m.default ? " selected" : ""}>${esc(m.id)} · ${m.price_in}/${m.price_out} $ par M</option>`).join("");
  $("t2iSelect").innerHTML = cfg.t2i.map((m) =>
    `<option value="${esc(m.id)}"${m.default ? " selected" : ""}>${esc(m.id)} · ${money(m.cost)}</option>`).join("");
  $("nInput").max = cfg.max_renders;
  $("nInput").value = Math.min(cfg.candidates_default, cfg.max_renders);
  showBudget(cfg);
}

async function loadRepresentations() {
  try {
    const data = await api("representations?item_class=" + encodeURIComponent(currentClass()));
    const items = data.items.length ? data.items : [{ REPRESENTATION: data.default }];
    $("repSelect").innerHTML = items.map((r) =>
      `<option value="${esc(r.REPRESENTATION)}"${r.REPRESENTATION === data.default ? " selected" : ""}>${esc(r.REPRESENTATION)}${r.REPRESENTATION_NAME ? " · " + esc(r.REPRESENTATION_NAME) : ""}</option>`).join("");
  } catch (err) {
    $("repSelect").innerHTML = '<option value="">défaut</option>';
  }
}

// ---------------------------------------------------------------------------
// Entity picking
// ---------------------------------------------------------------------------
let searchTimer = null;
function onEntitySearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("entitySearch").value.trim();
    try {
      const data = await api("entities?item_class=" + encodeURIComponent(currentClass()) +
        "&q=" + encodeURIComponent(q) + "&limit=30");
      $("entityResults").innerHTML = data.items.map((e) =>
        `<li data-id="${esc(e.id_item)}" data-name="${esc(e.name)}">${esc(e.name)} <small>#${esc(e.id_item)}${e.id_wikidata ? " · " + esc(e.id_wikidata) : ""}${genreTag(e)}</small></li>`).join("") ||
        '<li class="empty">Aucun résultat</li>';
    } catch (err) {
      toast(err.message, true);
    }
  }, 250);
}

function genreTag(e) {
  if (e.APPLIES_TO_MOVIE === undefined) return "";
  const tags = [];
  if (e.APPLIES_TO_MOVIE) tags.push("films");
  if (e.APPLIES_TO_SERIE) tags.push("séries");
  return tags.length ? " · " + tags.join(", ") : "";
}

function pickEntity(id, name) {
  state.entity = { id_item: Number(id), name };
  state.prep = null;
  state.desc = null;
  $("entityPicked").textContent = name + " (#" + id + ")";
  $("entityResults").innerHTML = "";
  $("previewBtn").disabled = false;
  $("previewBox").hidden = true;
  $("describeBox").hidden = true;
  $("jobBox").hidden = true;
}

// ---------------------------------------------------------------------------
// Workshop: preview -> describe -> render
// ---------------------------------------------------------------------------
function rendersCount() {
  const max = state.config ? state.config.max_renders : 4;
  return Math.max(1, Math.min(max, Number($("nInput").value) || 1));
}

async function doPreview() {
  if (!state.entity) return;
  $("previewBtn").disabled = true;
  try {
    const data = await api("preview", {
      item_class: currentClass(), id_item: state.entity.id_item,
      representation: $("repSelect").value, t2t_model: $("t2tSelect").value,
      t2i_model: $("t2iSelect").value, n: rendersCount(),
    });
    state.prep = data;
    state.desc = null;
    $("sourceKind").textContent = data.entity.source || "?";
    $("sourceText").textContent = data.source_text || "(aucun texte source : le modèle s'appuiera sur le nom seul)";
    $("t2tSystem").textContent = data.t2t.system;
    $("t2tPrompt").textContent = data.t2t.prompt;
    $("estimate").innerHTML = "Estimation : description <b>" + money(data.estimate.t2t) + "</b> + " +
      data.estimate.n + " image(s) avec " + esc(data.t2i.model) + " <b>" + money(data.estimate.t2i) +
      "</b> = <b>" + money(data.estimate.total) + "</b>. Reste aujourd'hui : " + money(data.remaining) + ".";
    $("describeBtn").textContent = "2. Décrire (≈ " + money(data.estimate.t2t) + ")";
    $("previewBox").hidden = false;
    $("describeBox").hidden = true;
    $("jobBox").hidden = true;
    showBudget(data);
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("previewBtn").disabled = false;
  }
}

function t2iPromptFor(text) {
  return state.prep ? state.prep.t2i.template.replace("{object_description}", text) : text;
}

function updateRenderButton() {
  const model = $("t2iSelect").value;
  const m = state.config.t2i.find((x) => x.id === model);
  const cost = m ? m.cost * rendersCount() : 0;
  $("renderBtn").textContent = "3. Produire " + rendersCount() + " image(s) avec " + model + " (≈ " + money(cost) + ")";
}

async function doDescribe() {
  if (!state.prep) return;
  $("describeBtn").disabled = true;
  $("describeBtn").textContent = "Description en cours…";
  try {
    const data = await api("describe", { prep_id: state.prep.prep_id, t2t_model: $("t2tSelect").value });
    state.desc = data;
    $("descText").value = data.text;
    $("descMeta").textContent = "Coût réel : " + money(data.cost) + " · " + data.input_tokens +
      " jetons en entrée, " + data.output_tokens + " en sortie · " + data.seconds + " s · description #" + data.id_description;
    $("t2iPrompt").textContent = data.t2i_prompt;
    $("describeBox").hidden = false;
    updateRenderButton();
    refreshBudget();
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("describeBtn").disabled = false;
    $("describeBtn").textContent = "2. Décrire de nouveau";
  }
}

async function doRender() {
  if (!state.desc) return;
  $("renderBtn").disabled = true;
  try {
    const data = await api("render", {
      id_description: state.desc.id_description, text: $("descText").value,
      t2i_model: $("t2iSelect").value, n: rendersCount(),
    });
    if (data.id_description !== state.desc.id_description) {
      state.desc.id_description = data.id_description;   // the corrected text became its own description
      state.desc.text = $("descText").value;
    }
    $("jobBox").hidden = false;
    $("jobTitle").textContent = "Rendu en cours avec " + $("t2iSelect").value + "…";
    $("jobImages").innerHTML = data.candidate_indexes.map((i) =>
      `<div class="thumb" id="pending-${i}"><div class="ph">candidate ${i}<br>en cours…</div></div>`).join("");
    pollJob(data.job_id);
  } catch (err) {
    toast(err.message, true);
    $("renderBtn").disabled = false;
  }
}

function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  const tick = async () => {
    try {
      const job = await api("jobs/" + jobId);
      for (const r of job.results) {
        const el = $("pending-" + r.candidate_index);
        if (el && !el.dataset.done) {
          el.dataset.done = "1";
          el.outerHTML = thumbHtml({
            ID_SYNTHETIC_IMAGE: r.id_synthetic_image, url: r.url, STATUS: r.status,
            CANDIDATE_INDEX: r.candidate_index, T2I_MODEL: r.model, GENERATION_COST: r.cost,
            FAILURE_REASON: r.failure_reason, T2I_SEED: r.seed,
          }, null, state.entity ? state.entity.name : "");
        }
      }
      if (job.status === "running") {
        $("jobTitle").textContent = "Rendu en cours avec " + job.model + "… " + job.results.length + "/" + job.n + " (" + job.elapsed + " s)";
        state.pollTimer = setTimeout(tick, 1500);
      } else {
        const ok = job.results.filter((r) => r.status === "generated").length;
        const labels = [...new Set(job.results.filter((r) => r.status !== "generated").map((r) => errorLabel(r.failure_reason)))];
        $("jobTitle").textContent = "Terminé : " + ok + "/" + job.n + " image(s) avec " + job.model + " en " + job.elapsed + " s" +
          (labels.length ? " · échecs : " + labels.join(", ").toLowerCase() + " (cliquer pour le détail)" : "") +
          (job.error ? " · " + job.error : "");
        $("renderBtn").disabled = false;
        refreshBudget();
        loadReview();
      }
    } catch (err) {
      toast(err.message, true);
      $("renderBtn").disabled = false;
    }
  };
  tick();
}

async function refreshBudget() {
  try { showBudget(await api("budget")); } catch (err) { /* the budget line is informative only */ }
}

// ---------------------------------------------------------------------------
// Review: one row per entity, candidates side by side
// ---------------------------------------------------------------------------
const candidateCache = new Map();   // id -> candidate row (for the lightbox)

function thumbHtml(c, chosenId, entityName) {
  if (c.ID_SYNTHETIC_IMAGE) candidateCache.set(String(c.ID_SYNTHETIC_IMAGE), Object.assign({ entityName }, c));
  const isChosen = chosenId && c.ID_SYNTHETIC_IMAGE === chosenId;
  const ok = c.STATUS === "generated" && c.url;
  const media = ok
    ? `<img loading="lazy" src="${esc(c.url)}" alt="">`
    : `<div class="ph fail" title="Cliquer pour le détail"><span class="icon">⚠</span>${esc(errorLabel(c.FAILURE_REASON))}</div>`;
  return `<div class="thumb${isChosen ? " chosen" : ""}" data-image="${esc(c.ID_SYNTHETIC_IMAGE || "")}">` +
    (isChosen ? '<span class="star">servie</span>' : "") + media +
    `<div class="tag" title="${esc(c.T2I_MODEL)}">#${esc(c.CANDIDATE_INDEX)} · ${esc(shortModel(c.T2I_MODEL))}</div></div>`;
}

// A provider error is long and technical: the thumbnail shows a short label, the full message
// is one click away in the lightbox.
function errorLabel(reason) {
  const r = String(reason || "").toLowerCase();
  if (r.includes("moderation") || r.includes("safety")) return "Refusée par la modération";
  if (r.includes("402") || r.includes("credit") || r.includes("resource_exhausted")) return "Crédit épuisé";
  if (r.includes("429") || r.includes("rate limit") || r.includes("throttl")) return "Limite de débit";
  if (r.includes("timed out") || r.includes("timeout") || r.includes("after the timeout")) return "Délai dépassé";
  if (r.startsWith("retired")) return "Modèle retiré";
  if (r.includes("aspect ratio")) return "Mauvais format";
  return "Échec du rendu";
}

function shortModel(model) {
  return String(model || "").split("/").pop();
}

async function loadReview() {
  const q = $("reviewSearch").value.trim();
  try {
    const data = await api("candidates?item_class=" + encodeURIComponent(currentClass()) +
      "&q=" + encodeURIComponent(q) + "&only=" + $("reviewOnly").value + "&limit=40");
    $("reviewRows").innerHTML = data.rows.map((row) => {
      const e = row.entity;
      const thumbs = row.candidates.length
        ? row.candidates.map((c) => thumbHtml(c, row.chosen, e.name)).join("")
        : '<span class="empty">aucune candidate</span>';
      return `<div class="row"><div class="name">${esc(e.name)}<small>#${esc(e.id_item)}${e.id_wikidata ? " · " + esc(e.id_wikidata) : ""}${row.manual ? " · choix manuel" : (row.chosen ? " · choix automatique" : "")}</small>` +
        `<button class="secondary" data-workshop="${esc(e.id_item)}" data-name="${esc(e.name)}">Atelier</button></div>` +
        `<div class="thumbs">${thumbs}</div></div>`;
    }).join("") || '<p class="empty">Rien à afficher pour ce filtre.</p>';
  } catch (err) {
    toast(err.message, true);
  }
}

function openLightbox(imageId) {
  const c = candidateCache.get(String(imageId));
  if (!c) return;
  state.lightboxImage = c;
  const ok = c.STATUS === "generated" && c.url;
  $("lbImg").hidden = !ok;
  $("lbError").hidden = !!ok;
  $("lbChoose").hidden = !ok;
  if (ok) {
    $("lbImg").src = c.url;
  } else {
    $("lbImg").removeAttribute("src");
    $("lbError").innerHTML = `<h4>⚠ ${esc(errorLabel(c.FAILURE_REASON))}</h4><p class="hint">Message du fournisseur :</p><pre>${esc(c.FAILURE_REASON || "aucun message")}</pre>`;
  }
  $("lbTitle").textContent = (c.entityName || "") + " · candidate " + c.CANDIDATE_INDEX;
  const rows = [
    ["Modèle image", c.T2I_MODEL], ["Modèle texte", c.T2T_LLM], ["Coût", money(c.GENERATION_COST)],
    ["Graine", c.T2I_SEED], ["Style", c.STYLE_VERSION], ["Représentation", c.REPRESENTATION],
    ["Produite le", c.TIM_GENERATED], ["Description", c.OBJECT_DESCRIPTION],
  ].filter((r) => r[1] !== undefined && r[1] !== null && r[1] !== "");
  $("lbMeta").innerHTML = rows.map((r) => `<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join("");
  $("lbPrompt").textContent = c.T2I_PROMPT || "(non conservé pour cette image)";
  $("lbMsg").textContent = "";
  $("lbChoose").disabled = false;
  $("lightbox").showModal();
}

async function chooseCurrent() {
  const c = state.lightboxImage;
  if (!c) return;
  $("lbChoose").disabled = true;
  try {
    await api("choose", { id_synthetic_image: c.ID_SYNTHETIC_IMAGE });
    $("lbMsg").textContent = "Choisie : c'est désormais l'image servie pour cette entité.";
    toast("Image choisie.");
    loadReview();
  } catch (err) {
    $("lbMsg").textContent = err.message;
    $("lbChoose").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
document.addEventListener("click", (ev) => {
  const li = ev.target.closest("#entityResults li[data-id]");
  if (li) { pickEntity(li.dataset.id, li.dataset.name); return; }
  const ws = ev.target.closest("button[data-workshop]");
  if (ws) { pickEntity(ws.dataset.workshop, ws.dataset.name); $("workshop").scrollIntoView({ behavior: "smooth" }); return; }
  const thumb = ev.target.closest(".thumb[data-image]");
  if (thumb && thumb.dataset.image) openLightbox(thumb.dataset.image);
});

$("classSelect").addEventListener("change", () => {
  state.entity = null;
  $("entityPicked").textContent = "Aucune entité choisie.";
  $("previewBtn").disabled = true;
  $("previewBox").hidden = true;
  $("describeBox").hidden = true;
  $("jobBox").hidden = true;
  $("entitySearch").value = "";
  loadRepresentations();
  onEntitySearch();
  loadReview();
});
$("entitySearch").addEventListener("input", onEntitySearch);
$("entitySearch").addEventListener("focus", onEntitySearch);
$("previewBtn").addEventListener("click", doPreview);
$("describeBtn").addEventListener("click", doDescribe);
$("renderBtn").addEventListener("click", doRender);
$("descText").addEventListener("input", () => { $("t2iPrompt").textContent = t2iPromptFor($("descText").value); });
$("t2iSelect").addEventListener("change", () => { if (state.desc) updateRenderButton(); });
$("nInput").addEventListener("change", () => { if (state.desc) updateRenderButton(); });
$("reviewRefresh").addEventListener("click", loadReview);
$("reviewOnly").addEventListener("change", loadReview);
$("reviewSearch").addEventListener("input", () => { clearTimeout(loadReview.timer); loadReview.timer = setTimeout(loadReview, 300); });
$("lbClose").addEventListener("click", () => $("lightbox").close());
$("lbChoose").addEventListener("click", chooseCurrent);
$("lightbox").addEventListener("click", (ev) => { if (ev.target === $("lightbox")) $("lightbox").close(); });

loadConfig()
  .then(() => { loadRepresentations(); loadReview(); })
  .catch((err) => toast("Configuration illisible : " + err.message, true));
