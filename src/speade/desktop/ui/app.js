// app.js -- page logic only. Talks to the backend EXCLUSIVELY through `api`
// (api.js); knows nothing about pywebview, HTTP, or Python.

const $ = (id) => document.getElementById(id);
let queue = [];
let pending = []; // inbox files still waiting to be processed
let selected = null;

// -------- tags view state, rebuilt whenever the selection changes. The tags
// view IS the document view: tree + continuous page stack with all boxes drawn.
let structTree = null; // the fetched tree for `selected`
let structSelectedRow = null; // the highlighted .tnode element
let pageObserver = null; // lazy-loads page images as bands scroll into view

// ------------------------------------------------- plain-language dictionaries
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
  if (pending.length) {
    const head = document.createElement("div");
    head.className = "qsection";
    head.textContent = `Waiting to process (${pending.length})`;
    box.appendChild(head);
    for (const name of pending) {
      const div = document.createElement("div");
      div.className = "qitem waiting";
      div.innerHTML =
        `<div class="name">${name}</div>` +
        `<div class="sub">${chip("added — press Process", "draft")}</div>`;
      box.appendChild(div);
    }
    const head2 = document.createElement("div");
    head2.className = "qsection";
    head2.textContent = "Processed documents";
    box.appendChild(head2);
  }
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
  if (!queue.length && !pending.length) {
    box.innerHTML = '<div class="qitem muted">No documents yet, add PDFs and press Process PDFs.</div>';
  }
  const n = pending.length;
  $("run").textContent = n ? `Process ${n} PDF${n === 1 ? "" : "s"}` : "Process PDFs";
}

async function refresh(keepSelection = true) {
  [queue, pending] = await Promise.all([api.listQueue(), api.listPending()]);
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

// What ACTUALLY happened to this document -- derived from the honest signals
// (ocr_layered, flags), not stages_applied, which lists stages that ran even
// when they skipped themselves (a digital PDF "ran" OCR as a pass-through).
function processedText(item) {
  if (item.flags.some((f) => f.startsWith("unreadable-"))) {
    return "Could not be processed — the file is unreadable";
  }
  if (item.flags.includes("tag-skipped-already-tagged")) {
    return "Already had accessibility tags — left as it was";
  }
  if (item.flags.includes("tag-skipped-needs-ocr")) {
    return "Scanned document — NOT tagged (text recognition did not run)";
  }
  const tagged = item.stages_applied.includes("tag");
  if (item.ocr_layered) {
    return "Scanned document — text recognised (OCR) and tagged";
  }
  if (tagged && item.route === "unknown") {
    return "Mixed content — tagged (check every part got covered)";
  }
  if (tagged) return "Digital document — tagged";
  if (item.stages_applied.includes("noop")) return "Copied (no processing configured)";
  return item.stages_applied.join(", ") || "(nothing)";
}

// Plain language for the veraPDF clause families a reviewer will actually meet;
// the raw codes stay visible in brackets for the Acrobat/IT conversation.
const CLAUSE_TEXT = [
  ["7.21", "fonts not embedded properly"],
  ["7.18", "form fields need attention"],
  ["7.3", "images missing descriptions"],
  ["7.2", "language settings"],
  ["7.1", "document title/settings"],
  ["6.2", "some content is not tagged"],
  ["5.", "tagging declaration"],
];

function veraIssuesText(clauses) {
  const labels = [];
  for (const clause of clauses) {
    const hit = CLAUSE_TEXT.find(([prefix]) => clause.startsWith(prefix));
    const label = hit ? hit[1] : `rule ${clause}`;
    if (!labels.includes(label)) labels.push(label);
  }
  return labels.join("; ");
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

  const clauses = item.verapdf_failed_clauses || [];
  const issueText = clauses.length
    ? `${clauses.length} issue${clauses.length === 1 ? "" : "s"}: ` +
      `${veraIssuesText(clauses)} — fix in Acrobat, or use your judgement` +
      ` <span class="muted">(${clauses.join(", ")})</span>`
    : "found issues";
  $("facts").innerHTML =
    `<dt>Processed</dt><dd>${processedText(item)}</dd>` +
    `<dt>Automatic check</dt><dd>${
      item.verapdf_passed === null
        ? "runs after processing and again when you decide"
        : item.verapdf_passed
          ? "passed (PDF/UA accessibility rules)"
          : issueText
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

  // the tags view IS the document view: tree + pages with all boxes drawn.
  loadStructure(item.file);
}

// ---------------------------------------------------- structure (tags) panel
// Plain-language names for the PDF structure types reviewers will meet.
const TYPE_TEXT = {
  Document: "Document",
  Part: "Part", Sect: "Section", Div: "Group", Art: "Article",
  P: "Paragraph",
  H: "Heading", H1: "Heading 1", H2: "Heading 2", H3: "Heading 3",
  H4: "Heading 4", H5: "Heading 5", H6: "Heading 6",
  L: "List", LI: "List item", Lbl: "Bullet / number", LBody: "List text",
  Table: "Table", TR: "Table row", TD: "Table cell", TH: "Table header",
  Figure: "Image", Formula: "Formula", Form: "Form field",
  Link: "Link", Span: "Text piece", Note: "Note", Caption: "Caption",
  TOC: "Contents list", TOCI: "Contents entry", BlockQuote: "Quotation",
};

function typeText(t) {
  return TYPE_TEXT[t] || t;
}

async function loadStructure(file) {
  structTree = null;
  structSelectedRow = null;
  if (pageObserver) {
    pageObserver.disconnect();
    pageObserver = null;
  }
  $("tag-tree").innerHTML = '<div class="muted">Reading the tag structure…</div>';
  $("page-stack").innerHTML = "";
  $("structure-note").hidden = true;
  const tree = await api.structureTree(file);
  if (selected !== file) return; // user moved on while we fetched
  structTree = tree;
  if (tree.error) {
    $("tag-tree").innerHTML = "";
    const note = $("structure-note");
    note.textContent = tree.error + " — this document cannot be displayed here.";
    note.hidden = false;
    return;
  }
  if (!tree.tagged) {
    $("tag-tree").innerHTML =
      '<div class="muted">No tags yet — this document has no accessibility structure.</div>';
  } else {
    renderTree();
  }
  renderPageStack(file);
}

// every leaf tag's box, grouped by page -- drawn permanently, Acrobat-style.
function collectLeafBoxes(nodes, out) {
  for (const node of nodes) {
    if (node.kids.length) collectLeafBoxes(node.kids, out);
    else if (node.box && node.page !== null) (out[node.page] ||= []).push(node);
  }
}

function boxStyle(el, box, size) {
  const [x0, y0, x1, y1] = box;
  el.style.left = `${(x0 / size.width) * 100}%`;
  el.style.top = `${((size.height - y1) / size.height) * 100}%`;
  el.style.width = `${((x1 - x0) / size.width) * 100}%`;
  el.style.height = `${((y1 - y0) / size.height) * 100}%`;
}

function nodeBox(node, size) {
  const div = document.createElement("div");
  div.className = "hlbox";
  boxStyle(div, node.box, size);
  div.title = typeText(node.type) + (node.text ? ` — ${node.text}` : "");
  div.onclick = () => selectNode(node, { scrollTree: true });
  return div;
}

function renderPageStack(file) {
  const stack = $("page-stack");
  stack.innerHTML = "";
  const boxesByPage = [];
  if (structTree.tagged) collectLeafBoxes(structTree.root, boxesByPage);
  const bands = [];
  structTree.pages.forEach((size, i) => {
    const band = document.createElement("div");
    band.className = "pageband";
    band.style.aspectRatio = `${size.width} / ${size.height}`;
    band.dataset.page = i;
    const num = document.createElement("span");
    num.className = "pagenum";
    num.textContent = `Page ${i + 1}`;
    band.appendChild(num);
    for (const node of boxesByPage[i] || []) {
      band.appendChild(nodeBox(node, size));
    }
    stack.appendChild(band);
    bands.push(band);
  });
  // pages render lazily as they scroll into view -- a 100-page scan must not
  // fetch 100 images up front. Sizes are known, so layout never jumps.
  pageObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const band = entry.target;
        pageObserver.unobserve(band);
        const index = parseInt(band.dataset.page, 10);
        api.pageImage(file, index).then((result) => {
          if (selected !== file || result.error || band.querySelector("img")) return;
          const img = document.createElement("img");
          img.alt = `Page ${index + 1}`;
          img.src = result.data_uri;
          band.prepend(img);
        });
      }
    },
    { root: $("page-side"), rootMargin: "600px" }
  );
  bands.forEach((b) => pageObserver.observe(b));
}

function renderTree() {
  const box = $("tag-tree");
  box.innerHTML = "";
  structSelectedRow = null;
  const build = (nodes, container) => {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.className = "tnode";
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = node.kids.length ? "▾" : "";
      const type = document.createElement("span");
      type.className = "ttype";
      type.textContent = typeText(node.type);
      const text = document.createElement("span");
      text.className = "ttext";
      text.textContent =
        node.text || (node.type === "Figure" ? node.alt || "(no description yet)" : "");
      row.append(caret, type, text);
      container.appendChild(row);
      let kidsBox = null;
      if (node.kids.length) {
        kidsBox = document.createElement("div");
        kidsBox.className = "tkids";
        build(node.kids, kidsBox);
        container.appendChild(kidsBox);
      }
      caret.onclick = (e) => {
        e.stopPropagation();
        if (!kidsBox) return;
        kidsBox.classList.toggle("collapsed");
        caret.textContent = kidsBox.classList.contains("collapsed") ? "▸" : "▾";
      };
      node.__row = row; // page-box clicks find their way back to this row
      row.onclick = () => selectNode(node);
    }
  };
  build(structTree.root, box);
  if (structTree.truncated) {
    const more = document.createElement("div");
    more.className = "muted";
    more.textContent = "…tree shortened (very large document)";
    box.appendChild(more);
  }
}

function selectNode(node, opts = {}) {
  if (structSelectedRow) structSelectedRow.classList.remove("selected");
  structSelectedRow = node.__row || null;
  if (structSelectedRow) {
    structSelectedRow.classList.add("selected");
    if (opts.scrollTree) {
      structSelectedRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  document.querySelectorAll(".hlbox.selected").forEach((el) => el.remove());
  if (node.box == null || node.page === null || !structTree) return;
  const band = $("page-stack").querySelector(`.pageband[data-page="${node.page}"]`);
  if (!band) return;
  const emphasis = document.createElement("div");
  emphasis.className = "hlbox selected";
  boxStyle(emphasis, node.box, structTree.pages[node.page]);
  band.appendChild(emphasis);
  // tree click -> bring the page location into view; box click -> stay put
  // (the user is already looking at it) and reveal the tree row instead.
  if (!opts.scrollTree) emphasis.scrollIntoView({ block: "center", behavior: "smooth" });
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
    const where = approve
      ? "Approved — moved to the approved folder, ready to share."
      : "Rejected — moved to the rejected folder. Fix it in Adobe Acrobat, then come back here and approve it.";
    $("gate-result").textContent = `${where} Recorded by ${result.reviewer}; ${verdict}.`;
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
      const processed = items.filter((i) => i.ok && !i.skipped).length;
      const skipped = items.filter((i) => i.skipped).length;
      const fails = items
        .filter((i) => !i.ok)
        .map((i) => `${i.file}: ${i.error}`)
        .join(" | ");
      const stopped = s.cancelled ? "Stopped early. " : "";
      const parts = [];
      if (processed) parts.push(`${processed} processed`);
      if (skipped) parts.push(`${skipped} already done (skipped)`);
      setStatus(
        items.length
          ? stopped + parts.join(", ") + "." + (fails ? ` Problems - ${fails}` : "")
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
    await refresh(); // they appear in "Waiting to process" immediately
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
  $("open-inbox").onclick = () => api.openInbox();
  $("open-outbox").onclick = () => api.openOutbox();
  $("approve").onclick = () => decide(true);
  $("reject").onclick = () => decide(false);
  $("history").onclick = toggleHistory;

  // full paths live in the folder buttons' tooltips, not the header.
  const ws = await api.workspace();
  $("open-inbox").title = ws.inbox;
  $("open-outbox").title = ws.outbox;
  $("reviewer").value = await api.reviewerDefault();
  await refresh(false);
}

init();
