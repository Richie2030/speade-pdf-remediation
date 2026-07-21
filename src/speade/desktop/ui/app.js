// app.js -- page logic only. Talks to the backend EXCLUSIVELY through `api`
// (api.js); knows nothing about pywebview, HTTP, or Python.

const $ = (id) => document.getElementById(id);
let queue = [];
let selected = null;
let previewUrl = null; // blob: URL of the current preview, revoked on change

// ------------------------------------------------- plain-language dictionaries
const ROUTE_TEXT = {
  born_digital: "Digital text",
  scanned: "Scanned images",
  unknown: "Mixed / unclear",
};

const STATUS_TEXT = {
  draft: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
  "invalid-sidecar": "Record damaged",
};

const STAGE_TEXT = {
  detect: "analysed",
  ocr: "text recognised (OCR)",
  tag: "tagged",
  noop: "copied",
};

const FLAG_TEXT = {
  "tag-skipped-needs-ocr": "Not tagged, needs text recognition first",
  "tag-skipped-already-tagged": "Already had tags, left untouched",
  "tag-skipped-unreadable": "Not tagged, the file is unreadable",
  "tag-ran-on-unknown-route": "Mixed content, check every part got tagged",
  "ocr-unavailable": "Text recognition is not installed on this PC",
  "ocr-failed": "Text recognition failed on this document",
  "ocr-timeout": "Text recognition took too long and was stopped",
  "ocr-skipped-unreadable": "No text recognition, the file is unreadable",
  "unreadable-encrypted-password-required": "Password-protected, cannot be processed",
};

function flagText(flag) {
  if (FLAG_TEXT[flag]) return FLAG_TEXT[flag];
  if (flag.startsWith("unreadable-corrupt")) return "File is damaged and cannot be read";
  return flag; // unknown flag: show it raw rather than hide it
}

function setStatus(text) {
  $("status").textContent = text || "";
}

function chip(text, cls) {
  return `<span class="chip ${cls || ""}">${text}</span>`;
}

function veraChip(item) {
  if (item.verapdf_passed === true) return chip("auto-check: passed", "pass");
  if (item.verapdf_passed === false) {
    const n = (item.verapdf_failed_clauses || []).length;
    return chip(`auto-check: ${n || "some"} issue${n === 1 ? "" : "s"}`, "fail");
  }
  return chip("not checked yet");
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
      `<div class="sub">${chip(STATUS_TEXT[item.status] || item.status, item.status)} ` +
      `${veraChip(item)}` +
      (item.flags.length ? ` ${chip(item.flags.length + " note(s)", "flag")}` : "") +
      `</div>`;
    div.onclick = () => select(item.file);
    box.appendChild(div);
  }
  if (!queue.length) {
    box.innerHTML = '<div class="qitem muted">No documents yet, add PDFs and press Process PDFs.</div>';
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
  $("history-pane").hidden = true;
  renderQueue();
  renderDetail();
}

function structureText(s) {
  if (s.error) return s.error;
  if (!s.tagged) return "No tags yet, this document has no accessibility structure.";
  const parts = [];
  if (s.headings) parts.push(`${s.headings} heading${s.headings === 1 ? "" : "s"}`);
  parts.push(`${s.paragraphs} paragraph${s.paragraphs === 1 ? "" : "s"}`);
  if (s.lists) parts.push(`${s.lists} list${s.lists === 1 ? "" : "s"}`);
  if (s.tables) parts.push(`${s.tables} table${s.tables === 1 ? "" : "s"}`);
  if (s.figures) {
    let f = `${s.figures} image${s.figures === 1 ? "" : "s"}`;
    if (s.figures_missing_alt) f += ` (${s.figures_missing_alt} missing a description, add it in Acrobat)`;
    parts.push(f);
  }
  return "Tagged: " + parts.join(", ");
}

async function renderDetail() {
  const item = queue.find((q) => q.file === selected);
  if (!item) return;
  $("detail-empty").hidden = true;
  $("detail-body").hidden = false;
  $("doc-name").textContent = item.file;
  $("gate-result").textContent = "";

  const clauses = (item.verapdf_failed_clauses || []).join(", ");
  const steps = item.stages_applied.map((s) => STAGE_TEXT[s] || s).join(", ");
  $("facts").innerHTML =
    `<dt>Document type</dt><dd>${ROUTE_TEXT[item.route] || item.route}</dd>` +
    `<dt>What was done</dt><dd>${steps || "(nothing)"}</dd>` +
    `<dt>Automatic check</dt><dd>${
      item.verapdf_passed === null
        ? "runs after processing and again when you decide"
        : item.verapdf_passed
          ? "passed (PDF/UA accessibility rules)"
          : "found issues" + (clauses ? ": " + clauses : "")
    }</dd>` +
    `<dt>Structure</dt><dd id="structure-fact">checking&hellip;</dd>` +
    `<dt>File check</dt><dd>${
      item.output_changed === true
        ? "Edited since processing (Acrobat fixes are fine), it is checked again when you decide"
        : item.output_changed === false
          ? "Unchanged since the app processed it"
          : "—"
    }</dd>` +
    `<dt>Status</dt><dd>${STATUS_TEXT[item.status] || item.status}${
      item.reviewer ? " by " + item.reviewer : ""
    }</dd>`;
  $("doc-flags").innerHTML = item.flags
    .map((f) => chip(flagText(f), "flag"))
    .join(" ");

  // structure summary arrives async; guard against a stale selection.
  api.structure(item.file).then((s) => {
    const cell = document.getElementById("structure-fact");
    if (cell && selected === item.file) cell.textContent = structureText(s);
  });

  // current title + language into the editable fields (also async + guarded).
  $("meta-result").textContent = "";
  $("doc-title").value = "";
  setLangFields("");
  $("doc-title").disabled = $("doc-lang").disabled = true;
  $("doc-lang-other").disabled = $("save-meta").disabled = true;
  api.docMetadata(item.file).then((m) => {
    if (selected !== item.file) return;
    if (m.error) {
      $("meta-result").textContent = m.error;
      return;
    }
    $("doc-title").disabled = $("doc-lang").disabled = false;
    $("doc-lang-other").disabled = $("save-meta").disabled = false;
    $("doc-title").value = m.title || "";
    setLangFields(m.lang || "");
  });

  // embedded preview via a blob: URL -- WebView2 refuses large data: URIs in
  // <embed>, but object URLs stream fine.
  const preview = $("preview");
  const note = $("preview-note");
  preview.removeAttribute("src");
  note.hidden = true;
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
  const loaded = await api.loadPdf(item.file);
  if (loaded.data_uri && selected === item.file) {
    const bytes = Uint8Array.from(atob(loaded.data_uri.split(",")[1]), (c) => c.charCodeAt(0));
    previewUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    preview.src = previewUrl;
  } else if (!loaded.data_uri) {
    note.textContent = loaded.error || "Preview unavailable - use Open in PDF viewer.";
    note.hidden = false;
  }
}

// The language dropdown covers the common cases; a code it does not know goes
// through the "Other…" free-text field (also how an unlisted existing /Lang shows).
function setLangFields(lang) {
  const dropdown = $("doc-lang");
  const known = [...dropdown.options].some((o) => o.value === lang && o.value !== "__other");
  dropdown.value = known ? lang : lang ? "__other" : "";
  $("doc-lang-other").value = known ? "" : lang;
  $("doc-lang-other").hidden = dropdown.value !== "__other";
}

function currentLang() {
  const dropdown = $("doc-lang");
  return dropdown.value === "__other" ? $("doc-lang-other").value.trim() : dropdown.value;
}

// ------------------------------------------------------------------ actions
async function saveMeta() {
  if (!selected) return;
  $("save-meta").disabled = true;
  $("meta-result").textContent = "Saving…";
  const result = await api.setDocMetadata(selected, $("doc-title").value.trim(), currentLang());
  $("save-meta").disabled = false;
  if (result.error) {
    $("meta-result").textContent = result.error;
    return;
  }
  $("meta-result").textContent = "Saved, the file now carries this title and language.";
  await refresh(); // re-render: the preview and file-check row reflect the new bytes
}

async function decide(approve) {
  const reviewer = $("reviewer").value.trim();
  if (!reviewer) {
    $("gate-result").textContent = "Enter your student number first.";
    return;
  }
  $("approve").disabled = $("reject").disabled = true;
  setStatus("Running the final automatic check…");
  try {
    const result = await api.decide(selected, reviewer, approve);
    const verdict = result.verapdf_passed
      ? "automatic check passed"
      : "automatic check found issues (" + (result.failed_clauses.join(", ") || "unlisted") + ")";
    $("gate-result").textContent =
      `Recorded: ${STATUS_TEXT[result.status] || result.status} by ${result.reviewer} - ${verdict}.`;
    await refresh();
  } catch (err) {
    $("gate-result").textContent = "Error: " + err;
  } finally {
    $("approve").disabled = $("reject").disabled = false;
    setStatus("");
  }
}

function showProgress(done, total, current) {
  $("progress-wrap").hidden = false;
  const bar = $("progress");
  bar.max = Math.max(total, 1);
  // `done` counts FINISHED files; nudge the bar into the file being worked on
  // so it visibly moves, and reaches exactly 100% when done === total.
  bar.value = Math.min(done + 0.5, total);
  $("progress-text").textContent = total
    ? done >= total
      ? `${total} of ${total} - done`
      : `${done + 1} of ${total}` + (current ? ` - ${current}` : "")
    : "starting…";
}

async function stopBatch() {
  $("stop").disabled = true;
  $("stop").textContent = "Stopping…";
  setStatus("Stopping after the current document…");
  await api.runBatchCancel();
}

async function runBatch() {
  $("run").disabled = true;
  setStatus("");
  const started = await api.runBatchStart();
  if (started.error) {
    setStatus(started.error);
    $("run").disabled = false;
    return;
  }
  $("stop").hidden = false;
  $("stop").disabled = false;
  $("stop").textContent = "Stop";
  showProgress(0, 0, "");
  const poll = setInterval(async () => {
    const s = await api.runBatchStatus();
    if (s.running) {
      showProgress(s.done, s.total, s.current);
      return;
    }
    clearInterval(poll);
    $("stop").hidden = true; // the batch is over: nothing left to stop
    $("run").disabled = false;
    if (s.total && !s.cancelled) {
      showProgress(s.total, s.total, ""); // fill to 100% before hiding
      setTimeout(() => ($("progress-wrap").hidden = true), 1500);
    } else {
      $("progress-wrap").hidden = true;
    }
    if (s.error) {
      setStatus("Processing failed: " + s.error);
    } else {
      const items = s.items || [];
      const ok = items.filter((i) => i.ok).length;
      const fails = items
        .filter((i) => !i.ok)
        .map((i) => `${i.file}: ${i.error}`)
        .join(" | ");
      const stopped = s.cancelled ? "Stopped early. " : "";
      setStatus(
        items.length
          ? stopped + `${ok} of ${items.length} processed.` + (fails ? ` Problems - ${fails}` : "")
          : stopped + (s.cancelled ? "Nothing was processed." : "No PDFs in the input folder.")
      );
    }
    await refresh();
  }, 400);
}

async function addPdfs() {
  const result = await api.addPdfs();
  if (result.copied && result.copied.length) {
    setStatus(`Added: ${result.copied.join(", ")} - now press Process PDFs.`);
  } else if (result.error) {
    setStatus(result.error);
  }
}

// ------------------------------------------------------------------ history
function historyRow(e) {
  const when = e.ts ? new Date(e.ts).toLocaleString() : "-";
  const file = e.file || "-";
  let what;
  if (e.event === "run") {
    const steps = (e.stages_applied || []).map((s) => STAGE_TEXT[s] || s).join(", ");
    what = `processed (${steps || "no steps"})`;
  } else if (e.event === "verify") {
    what =
      `${STATUS_TEXT[e.decision] || e.decision} by ${e.reviewer}` +
      ` - automatic check ${e.verapdf_passed ? "passed" : "found issues"}`;
  } else {
    what = e.event;
  }
  return `<tr><td>${when}</td><td>${file}</td><td>${what}</td></tr>`;
}

async function toggleHistory() {
  const pane = $("history-pane");
  if (!pane.hidden) {
    pane.hidden = true;
    $(selected ? "detail-body" : "detail-empty").hidden = false;
    return;
  }
  const events = await api.auditLog(200);
  $("history-table").querySelector("tbody").innerHTML = events.map(historyRow).join("");
  $("detail-body").hidden = true;
  $("detail-empty").hidden = true;
  pane.hidden = false;
}

// -------------------------------------------------------------------- init
async function init() {
  $("run").onclick = runBatch;
  $("stop").onclick = stopBatch;
  $("save-meta").onclick = saveMeta;
  $("doc-lang").onchange = () => {
    $("doc-lang-other").hidden = $("doc-lang").value !== "__other";
    if (!$("doc-lang-other").hidden) $("doc-lang-other").focus();
  };
  $("refresh").onclick = () => refresh();
  $("add").onclick = addPdfs;
  $("open-outbox").onclick = () => api.openOutbox();
  $("open-viewer").onclick = () => selected && api.openOutput(selected);
  $("approve").onclick = () => decide(true);
  $("reject").onclick = () => decide(false);
  $("history").onclick = toggleHistory;

  const ws = await api.workspace();
  $("paths").textContent = `input folder: ${ws.inbox}    ·    output folder: ${ws.outbox}`;
  $("reviewer").value = await api.reviewerDefault();
  await refresh(false);
}

init();
