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
let showLinks = false; // inline links hidden by default (see isHiddenLink)
let selectedNode = null; // the tag currently open in the editor
let tagTypes = []; // assignable tag types, from the backend

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
        `<div class="sub">${chip("added, press Process", "draft")}</div>`;
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
      (item.output_changed === true ? ` ${chip("edited", "edited")}` : "") +
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
    return "Could not be processed, the file is unreadable";
  }
  if (item.flags.includes("tag-skipped-already-tagged")) {
    return "Already had accessibility tags, left as it was";
  }
  if (item.flags.includes("tag-skipped-needs-ocr")) {
    return "Scanned document, NOT tagged (text recognition did not run)";
  }
  const tagged = item.stages_applied.includes("tag");
  if (item.ocr_layered) {
    return "Scanned document, text recognised (OCR) and tagged";
  }
  if (tagged && item.route === "unknown") {
    return "Mixed content, tagged (check every part got covered)";
  }
  if (tagged) return "Digital document, tagged";
  if (item.stages_applied.includes("noop")) return "Copied (no processing configured)";
  return item.stages_applied.join(", ") || "(nothing)";
}

// Plain language for the veraPDF clause families a reviewer will actually meet;
// the raw codes stay visible in brackets for the Acrobat/IT conversation.
// Longest prefix wins, so specific sub-clauses beat their family. Codes are
// clause + test number from ISO 14289-1; docs/verapdf-clauses.md has all 106.
const CLAUSE_TEXT = [
  ["7.18.5", "links not properly tagged"],
  ["7.18.6", "form fields need labels"],
  ["7.18", "annotations (links, comments, forms) need attention"],
  ["7.21", "fonts not embedded properly"],
  ["7.9", "footnotes need unique IDs"],
  ["7.7", "formulas missing descriptions"],
  ["7.5", "table headers need attention"],
  ["7.4", "heading levels are wrong (skipped or mixed)"],
  ["7.3", "images missing descriptions"],
  ["7.2", "language, table, list or contents structure"],
  ["7.1", "tagging fundamentals or document title"],
  ["6.2", "not declared as a tagged PDF"],
  ["6.1", "PDF file format problem"],
  ["5", "PDF/UA declaration missing"],
];

function veraIssuesText(clauses) {
  const labels = [];
  for (const clause of clauses) {
    const hits = CLAUSE_TEXT.filter(([prefix]) => clause.startsWith(prefix));
    hits.sort((a, b) => b[0].length - a[0].length); // most specific match wins
    const label = hits.length ? hits[0][1] : `rule ${clause}`;
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
    if (s.figures_missing_alt) {
      f += ` (${s.figures_missing_alt} still need a description, marked below)`;
    }
    parts.push(f);
  }
  let text = "Tagged: " + parts.join(", ");
  // heading skips are veraPDF clause 7.4.2-1 and are fixed by retagging, so
  // name them here rather than leaving the reviewer to spot them.
  if (s.heading_skips && s.heading_skips.length) {
    text += `. Heading levels skip (${s.heading_skips.join(", ")}) - retag to fix`;
  }
  return text;
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
      `${veraIssuesText(clauses)} - fix in Acrobat, or use your judgement` +
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
    `<dt>Status</dt><dd>${STATUS_TEXT[item.status] || item.status}${
      item.reviewer ? " by " + item.reviewer : ""
    }</dd>`;
  $("doc-flags").innerHTML = item.flags
    .map((f) => chip(flagText(f), "flag"))
    .join(" ");

  // "has this file changed since the app made it" -- a banner the reviewer
  // cannot miss (the old one-line fact row was too quiet), plus the queue chip.
  $("edited-banner").hidden = item.output_changed !== true;
  $("revert").hidden = item.output_changed !== true;
  const undos = item.undo_depth || 0;
  $("undo-last").hidden = undos === 0;
  $("undo-last").textContent =
    undos > 1 ? `Undo last change (${undos} available)` : "Undo last change";

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

async function loadStructure(file, retrying = false) {
  structTree = null;
  structSelectedRow = null;
  if (pageObserver) {
    pageObserver.disconnect();
    pageObserver = null;
  }
  $("tag-tree").innerHTML = '<div class="muted">Reading the tag structure…</div>';
  $("page-stack").innerHTML = "";
  $("structure-note").hidden = true;
  $("tag-editor").hidden = true; // nothing selected in the new document yet
  selectedNode = null;
  const tree = await api.structureTree(file);
  if (selected !== file) return; // user moved on while we fetched
  structTree = tree;
  if (tree.error) {
    $("tag-tree").innerHTML = "";
    const note = $("structure-note");
    note.hidden = false;
    const item = queue.find((q) => q.file === file);
    const unreadable = item && item.flags.some((f) => f.startsWith("unreadable-"));
    if (unreadable) {
      note.textContent =
        "This document is password-protected or damaged, so it cannot be opened, " +
        "processed, or displayed. Reject it and ask for a usable copy of the file.";
      return;
    }
    // a readable document that fails to open is usually mid-(re)write by a
    // running batch; say so -- and retry once for any other transient state.
    const status = await api.runBatchStatus().catch(() => ({}));
    if (status.running) {
      note.textContent =
        "This document is being processed right now - it will appear when processing finishes.";
      return;
    }
    if (!retrying) {
      note.textContent = "Could not open the document - retrying…";
      setTimeout(() => selected === file && loadStructure(file, true), 1500);
      return;
    }
    note.textContent = tree.error + " - this document cannot be displayed here.";
    return;
  }
  const links = tree.tagged ? countLinks(tree.root) : 0;
  const filter = $("link-filter");
  filter.hidden = links === 0;
  $("link-count").textContent = `Show ${links} link${links === 1 ? "" : "s"} (citations, URLs)`;
  $("show-links").checked = showLinks;
  if (!tree.tagged) {
    $("tag-tree").innerHTML =
      '<div class="muted">No tags yet - this document has no accessibility structure.</div>';
  } else {
    renderTree();
  }
  renderPageStack(file);
}

// Inline links (hyperlinked citations) are content, but an academic paper can
// carry hundreds of them -- they bury the real structure in both the tree and
// the page boxes. They are hidden by default and restored by the checkbox;
// this is a VIEW filter only, the tags in the PDF are untouched.
function isHiddenLink(node) {
  return node.type === "Link" && !showLinks;
}

function countLinks(nodes) {
  let n = 0;
  for (const node of nodes) n += (node.type === "Link" ? 1 : 0) + countLinks(node.kids);
  return n;
}

// every leaf tag's box, grouped by page -- drawn permanently, Acrobat-style.
function collectLeafBoxes(nodes, out) {
  for (const node of nodes) {
    if (isHiddenLink(node)) continue;
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
  div.title = typeText(node.type) + (node.text ? ` - ${node.text}` : "");
  div.onclick = () => selectNode(node, { scrollTree: true });
  // Acrobat-style chip at the box's top-left naming the tag type.
  const tag = document.createElement("span");
  tag.className = "btag";
  tag.textContent = node.type;
  div.appendChild(tag);
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

// Some tags carry no text of their own because it lives in a child (a title
// wrapped in a link, for example). Borrow the descendants' text so the row
// reads as something instead of blank.
function descendantText(node) {
  const parts = [];
  const walk = (n) => {
    for (const kid of n.kids) {
      if (kid.text) parts.push(kid.text);
      walk(kid);
    }
  };
  walk(node);
  return parts.join(" ").slice(0, 120);
}

function renderTree() {
  const box = $("tag-tree");
  box.innerHTML = "";
  structSelectedRow = null;
  const build = (nodes, container) => {
    for (const node of nodes) {
      if (isHiddenLink(node)) continue;
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
        node.text ||
        (node.type === "Figure" ? node.alt || "(no description yet)" : "") ||
        descendantText(node); // a title whose text sits in a child link etc.
      row.append(caret, type, text);
      // an image with no description is the one thing a reviewer must not miss
      if ((node.type === "Figure" || node.type === "Formula") && !node.alt) {
        row.classList.add("needs-attention");
        const warn = document.createElement("span");
        warn.className = "tflag";
        warn.textContent = "needs description";
        row.appendChild(warn);
      }
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

// ------------------------------------------------------------ tag editing
// The two corrections that used to force an Acrobat trip: retagging (a heading
// the engine called a paragraph) and writing an image description. Both write
// straight into the PDF, so the document then reads "edited since processing".
function showEditor(node) {
  selectedNode = node;
  $("tag-editor").hidden = false;
  $("editor-result").textContent = "";
  const preview = node.text ? ` - "${node.text.slice(0, 60)}"` : "";
  $("editor-what").textContent = `Selected: ${typeText(node.type)}${preview}`;
  const select = $("tag-type");
  if (!select.options.length) {
    for (const t of tagTypes) {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = `${typeText(t)} (${t})`;
      select.appendChild(opt);
    }
  }
  select.value = tagTypes.includes(node.type) ? node.type : "";
  $("figure-alt").value = node.alt || "";
  // a description belongs on pictures; offer it there (and on anything that
  // already carries one) rather than on every paragraph.
  $("alt-row").hidden = !(node.type === "Figure" || node.type === "Formula" || node.alt);
}

async function afterEdit(result, message) {
  if (result.error) {
    $("editor-result").textContent = result.error;
    return;
  }
  const verdict = result.verapdf_passed
    ? "automatic check now passes"
    : `automatic check: ${(result.failed_clauses || []).length} issue(s) left`;
  $("editor-result").textContent = `${message} - ${verdict}.`;
  const file = selected;
  const nodeId = selectedNode ? selectedNode.id : null;
  await refresh(); // re-reads the queue: the "edited" chip + banner appear
  if (selected === file) {
    await loadStructure(file); // rebuild the tree from the changed PDF
    const again = findNodeById(structTree && structTree.root, nodeId);
    if (again) selectNode(again, { scrollTree: true });
  }
}

function findNodeById(nodes, id) {
  for (const node of nodes || []) {
    if (node.id === id) return node;
    const hit = findNodeById(node.kids, id);
    if (hit) return hit;
  }
  return null;
}

async function saveTagType() {
  if (!selectedNode) return;
  const newType = $("tag-type").value;
  if (!newType || newType === selectedNode.type) return;
  $("save-tag-type").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.setTagType(selected, selectedNode.id, newType);
  $("save-tag-type").disabled = false;
  await afterEdit(result, `Changed to ${typeText(newType)}`);
}

async function saveAlt() {
  if (!selectedNode) return;
  $("save-alt").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.setFigureAlt(selected, selectedNode.id, $("figure-alt").value);
  $("save-alt").disabled = false;
  await afterEdit(result, $("figure-alt").value.trim() ? "Description saved" : "Description cleared");
}

async function unwrapTag() {
  if (!selectedNode) return;
  const what = typeText(selectedNode.type);
  $("unwrap-tag").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.unwrapTag(selected, selectedNode.id);
  $("unwrap-tag").disabled = false;
  if (result.error) {
    $("editor-result").textContent = result.error;
    return;
  }
  selectedNode = null; // the tag no longer exists
  await afterEdit(result, `${what} tag removed, its contents kept`);
  $("tag-editor").hidden = true;
}

async function markDecorative() {
  if (!selectedNode) return;
  const what = typeText(selectedNode.type);
  // decoration is skipped by screen readers: if this tag carries real text,
  // make the reviewer confirm rather than silently hiding content.
  if (selectedNode.text && selectedNode.text.trim().length > 3) {
    const ok = window.confirm(
      `This ${what} contains text:\n\n"${selectedNode.text.slice(0, 120)}"\n\n` +
        "Marking it decorative removes it from the reading order, so a screen " +
        "reader will not read it out. Do that only for decoration (borders, " +
        "page numbers, background scans).\n\nContinue?"
    );
    if (!ok) return;
  }
  $("make-decorative").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.makeDecorative(selected, selectedNode.id);
  $("make-decorative").disabled = false;
  // the tag is gone from the tree, so there is nothing to re-select afterwards
  selectedNode = null;
  await afterEdit(result, `${what} is now decoration (no description needed)`);
  $("tag-editor").hidden = true;
}

async function moveTag(delta) {
  if (!selectedNode) return;
  const button = delta < 0 ? $("move-earlier") : $("move-later");
  button.disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.moveTag(selected, selectedNode.id, delta);
  button.disabled = false;
  if (result.moved === false) {
    $("editor-result").textContent = "Already at the " + (delta < 0 ? "start" : "end") +
      " of its group. Bigger moves are an Acrobat job.";
    return;
  }
  await afterEdit(result, `Moved ${delta < 0 ? "earlier" : "later"} in the reading order`);
}

async function removeAllTags() {
  if (!selected) return;
  if (!window.confirm(
    "Remove the whole tag structure from this document?\n\n" +
    "The document stays readable but loses all accessibility tagging, so it " +
    "must be tagged again (in Acrobat, or by pressing Undo all edits).")) {
    return;
  }
  const file = selected;
  $("remove-tags").disabled = true;
  setStatus("Removing the tag structure…");
  const result = await api.removeAllTags(file);
  $("remove-tags").disabled = false;
  setStatus(result.error ? result.error : "Tag structure removed.");
  selectedNode = null;
  $("tag-editor").hidden = true;
  await refresh();
  if (selected === file) await loadStructure(file);
}

async function undoLast() {
  if (!selected) return;
  const file = selected;
  $("undo-last").disabled = true;
  setStatus("Undoing the last change…");
  const result = await api.undoLastEdit(file);
  $("undo-last").disabled = false;
  setStatus(result.error ? result.error : "Last change undone.");
  selectedNode = null;
  $("tag-editor").hidden = true;
  await refresh();
  if (selected === file) await loadStructure(file);
}

async function revertDocument() {
  if (!selected) return;
  const file = selected;
  $("revert").disabled = true;
  setStatus("Undoing all edits: reprocessing the original document…");
  const result = await api.reprocess(file);
  $("revert").disabled = false;
  setStatus(result.error ? result.error : "Reprocessed from the original document.");
  await refresh();
  if (selected === file) await loadStructure(file);
}

function selectNode(node, opts = {}) {
  showEditor(node);
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
    // refresh FIRST: renderDetail clears gate-result, so the outcome message
    // must be written after the re-render or the reviewer never sees it
    // (found by the automated browser QA run).
    await refresh();
    const where = approve
      ? "Approved - moved to the approved folder, ready to upload."
      : "Rejected - moved to the rejected folder. Fix it in Adobe Acrobat, then come back here and approve it.";
    $("gate-result").textContent = `${where} Recorded by ${result.reviewer}; ${verdict}.`;
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
// Reviewer edits are part of the trust trail, so History must read them in
// plain language instead of showing the raw event name.
const EDIT_TEXT = {
  "edit-tag": (e) => `retagged ${typeText(e.from)} as ${typeText(e.to)}`,
  "edit-alt": (e) =>
    e.described ? "image description added" : "image description cleared",
  "edit-decorative": (e) => `${typeText(e.was)} marked decorative`,
  "edit-unwrap": (e) => `${typeText(e.was)} tag removed, contents kept`,
  "edit-order": (e) => `a tag moved ${e.direction} in the reading order`,
  "edit-remove-tags": () => "all tags removed",
  "edit-undo": () => "last change undone",
  "edit-metadata": () => "title / language saved",
};

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
  } else if (EDIT_TEXT[e.event]) {
    what = EDIT_TEXT[e.event](e);
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
  $("show-links").onchange = () => {
    showLinks = $("show-links").checked;
    if (structTree && structTree.tagged && selected) {
      renderTree();
      renderPageStack(selected);
    }
  };
  $("save-tag-type").onclick = saveTagType;
  $("save-alt").onclick = saveAlt;
  $("make-decorative").onclick = markDecorative;
  $("unwrap-tag").onclick = unwrapTag;
  $("move-earlier").onclick = () => moveTag(-1);
  $("move-later").onclick = () => moveTag(1);
  $("remove-tags").onclick = removeAllTags;
  $("undo-last").onclick = undoLast;
  $("revert").onclick = revertDocument;
  $("figure-alt").onkeydown = (e) => {
    if (e.key === "Enter") saveAlt();
  };
  tagTypes = await api.tagTypes();
  $("help").onclick = () => ($("help-overlay").hidden = false);
  $("help-close").onclick = () => ($("help-overlay").hidden = true);
  $("help-overlay").onclick = (e) => {
    if (e.target === $("help-overlay")) $("help-overlay").hidden = true;
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("help-overlay").hidden = true;
  });

  // full paths live in the folder buttons' tooltips, not the header.
  const ws = await api.workspace();
  $("open-inbox").title = ws.inbox;
  $("open-outbox").title = ws.outbox;
  $("reviewer").value = await api.reviewerDefault();
  await refresh(false);
}

init();
