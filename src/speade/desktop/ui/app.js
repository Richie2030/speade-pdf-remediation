// app.js -- page logic only. Talks to the backend EXCLUSIVELY through `api`
// (api.js); knows nothing about pywebview, HTTP, or Python.

const $ = (id) => document.getElementById(id);
let queue = [];
let selected = null;

function setStatus(text) {
  $("status").textContent = text || "";
}

function chip(text, cls) {
  return `<span class="chip ${cls || ""}">${text}</span>`;
}

function veraChip(item) {
  if (item.verapdf_passed === true) return chip("veraPDF pass", "pass");
  if (item.verapdf_passed === false) return chip("veraPDF fail", "fail");
  return chip("veraPDF: not checked");
}

// ------------------------------------------------------------------- queue
function renderQueue() {
  const box = $("queue");
  box.innerHTML = "";
  for (const item of queue) {
    const div = document.createElement("div");
    div.className = "qitem" + (selected === item.file ? " selected" : "");
    div.innerHTML =
      `<div class="name">${item.file}</div>` +
      `<div class="sub">${chip(item.status, item.status)} ${veraChip(item)}` +
      (item.flags.length ? ` ${chip(item.flags.length + " flag(s)", "flag")}` : "") +
      ` &middot; ${item.route}</div>`;
    div.onclick = () => select(item.file);
    box.appendChild(div);
  }
  if (!queue.length) {
    box.innerHTML = '<div class="qitem muted">Queue is empty — run a batch.</div>';
  }
}

async function refresh(keepSelection = true) {
  queue = await api.listQueue();
  if (!keepSelection || !queue.some((q) => q.file === selected)) selected = null;
  renderQueue();
  if (selected) renderDetail();
  else {
    $("detail-body").hidden = true;
    $("detail-empty").hidden = false;
  }
}

// ------------------------------------------------------------------ detail
function select(file) {
  selected = file;
  renderQueue();
  renderDetail();
}

async function renderDetail() {
  const item = queue.find((q) => q.file === selected);
  if (!item) return;
  $("detail-empty").hidden = true;
  $("detail-body").hidden = false;
  $("doc-name").textContent = item.file;
  $("gate-result").textContent = "";

  const clauses = (item.verapdf_failed_clauses || []).join(", ");
  $("facts").innerHTML =
    `<dt>route</dt><dd>${item.route}</dd>` +
    `<dt>stages</dt><dd>${item.stages_applied.join(" → ") || "(none)"}</dd>` +
    `<dt>status</dt><dd>${item.status}${item.reviewer ? " by " + item.reviewer : ""}</dd>` +
    `<dt>veraPDF</dt><dd>${
      item.verapdf_passed === null
        ? "checked when you decide"
        : item.verapdf_passed
          ? "passed"
          : "failed" + (clauses ? ": " + clauses : "")
    }</dd>`;
  $("doc-flags").innerHTML = item.flags.map((f) => chip(f, "flag")).join(" ");

  // embedded preview, with a graceful note when the doc is too large/missing.
  const preview = $("preview");
  const note = $("preview-note");
  preview.removeAttribute("src");
  note.hidden = true;
  const loaded = await api.loadPdf(item.file);
  if (loaded.data_uri) {
    preview.src = loaded.data_uri;
  } else {
    note.textContent = loaded.error || "preview unavailable — use Open in viewer";
    note.hidden = false;
  }
}

// ------------------------------------------------------------------ actions
async function decide(approve) {
  const reviewer = $("reviewer").value.trim();
  if (!reviewer) {
    $("gate-result").textContent = "Enter the reviewer name (your student number) first.";
    return;
  }
  $("approve").disabled = $("reject").disabled = true;
  setStatus("Running the veraPDF gate…");
  try {
    const result = await api.decide(selected, reviewer, approve);
    const verdict = result.verapdf_passed
      ? "veraPDF passed"
      : "veraPDF FAILED (" + (result.failed_clauses.join(", ") || "no clause list") + ")";
    $("gate-result").textContent = `${verdict} — recorded: ${result.status} by ${result.reviewer}.`;
    await refresh();
  } catch (err) {
    $("gate-result").textContent = "Error: " + err;
  } finally {
    $("approve").disabled = $("reject").disabled = false;
    setStatus("");
  }
}

async function runBatch() {
  $("run").disabled = true;
  setStatus("Running the pipeline over the inbox…");
  try {
    const items = await api.runBatch();
    const ok = items.filter((i) => i.ok).length;
    const fails = items
      .filter((i) => !i.ok)
      .map((i) => `${i.file}: ${i.error}`)
      .join(" | ");
    setStatus(
      items.length
        ? `${ok}/${items.length} processed.` + (fails ? ` Failures — ${fails}` : "")
        : "No PDFs in the inbox."
    );
    await refresh();
  } catch (err) {
    setStatus("Batch error: " + err);
  } finally {
    $("run").disabled = false;
  }
}

async function addPdfs() {
  const result = await api.addPdfs();
  if (result.copied && result.copied.length) {
    setStatus(`Added to inbox: ${result.copied.join(", ")} — press Run batch.`);
  } else if (result.error) {
    setStatus(result.error);
  }
}

// -------------------------------------------------------------------- init
async function init() {
  $("run").onclick = runBatch;
  $("refresh").onclick = () => refresh();
  $("add").onclick = addPdfs;
  $("open-outbox").onclick = () => api.openOutbox();
  $("open-viewer").onclick = () => selected && api.openOutput(selected);
  $("approve").onclick = () => decide(true);
  $("reject").onclick = () => decide(false);

  const ws = await api.workspace();
  $("paths").textContent =
    `inbox: ${ws.inbox}   ·   outbox: ${ws.outbox}   ·   ` +
    `pipeline: ${Object.values(ws.stages).join(" → ")}   ·   gate: ${ws.verapdf_profile}`;
  $("reviewer").value = await api.reviewerDefault();
  await refresh(false);
}

init();
