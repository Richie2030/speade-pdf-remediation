// app.js -- page logic only. Talks to the backend EXCLUSIVELY through `api`
// (api.js); knows nothing about pywebview, HTTP, or Python.

const $ = (id) => document.getElementById(id);
// respect the OS "reduce motion" setting: jump instead of animating scrolls
const SCROLL = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
let queue = [];
let pending = []; // inbox rel-ids still waiting to be processed
let selected = null;
// the Student Partner's module: everything is grouped per module, and Add /
// Process work only for the module typed here (persisted across restarts).
let moduleCode = "";
try {
  moduleCode = localStorage.getItem("speade-module") || "";
} catch (e) { /* storage unavailable: the box just starts empty */ }
const collapsedOverride = new Map(); // module key -> user's explicit open/closed
const doneOpen = new Set(); // module keys whose "Done" section is expanded
let knownModules = []; // existing module folders, for case-snapping the box
let batchRunning = false; // renderQueue must not re-enable Process mid-batch
let autoCheck = false; // run the accessibility check after EVERY edit (Settings)
// One edit at a time. A drag now applies on release, so a second gesture
// during the ~1s save would send a request built from the PRE-edit tree: the
// backend serialises the writes, but the second one's ids/indexes are stale.
// Refusing to start a second edit is the honest fix.
let editBusy = false;
// a drag's trailing click must not also open the box it ended on. The click
// lands on the nearest common ancestor of press and release, which is NOT the
// band when the drag ends outside it -- so a flag, not a band listener.
let dragJustEnded = false;

function setEditBusy(busy) {
  editBusy = busy;
  document.body.classList.toggle("edit-busy", busy);
}
// documents/modules the reviewer has removed from the side panel. A view
// filter only: every file stays on disk, and "Show hidden" brings them back.
let hiddenDocs = new Set();
let hiddenModules = new Set();
try {
  autoCheck = localStorage.getItem("speade-auto-check") === "1";
  hiddenDocs = new Set(JSON.parse(localStorage.getItem("speade-hidden-docs") || "[]"));
  hiddenModules = new Set(JSON.parse(localStorage.getItem("speade-hidden-modules") || "[]"));
} catch (e) { /* storage unavailable: defaults are fine */ }
let showHidden = false; // temporarily reveal what has been hidden

function saveHidden() {
  try {
    localStorage.setItem("speade-hidden-docs", JSON.stringify([...hiddenDocs]));
    localStorage.setItem("speade-hidden-modules", JSON.stringify([...hiddenModules]));
  } catch (e) { /* storage unavailable: hiding still works for this session */ }
}

// filenames are user/browser-supplied and land in innerHTML: escape them.
function esc(text) {
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

// "MG2001/notes.pdf" -> ["MG2001", "notes.pdf"]; "notes.pdf" -> [null, ...]
function splitId(relid) {
  const at = relid.indexOf("/");
  return at === -1 ? [null, relid] : [relid.slice(0, at), relid.slice(at + 1)];
}

// -------- tags view state, rebuilt whenever the selection changes. The tags
// view IS the document view: tree + continuous page stack with all boxes drawn.
let structTree = null; // the fetched tree for `selected`
let structSelectedRow = null; // the highlighted .tnode element
let pageObserver = null; // lazy-loads page images as bands scroll into view
let loadedBands = []; // bands holding a live page image, oldest first (LRU cap)
let showLinks = false; // inline links hidden by default (see isHiddenLink)
let selectedNode = null; // the tag currently open in the editor
let tagTypes = []; // assignable tag types, from the backend
let boxesByPage = []; // leaf tags per page, for marquee hit-testing
let linesByPage = []; // individual lines per page, for carving out a new tag
let untaggedByPage = []; // content with NO tag at all, taggable by a drag
let bulkSelected = []; // whole tags captured by the last marquee drag
let bulkLines = []; // individual lines captured by the last marquee drag
let bulkUntagged = []; // untagged pieces captured by the last marquee drag
let altWalk = null; // the Set-alternate-text stepper: { figures, at, savedAny }

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
  "tag-skipped-already-tagged": "Already had tags, kept them (standards metadata topped up)",
  "tag-skipped-unreadable": "Not tagged, the file is unreadable",
  "tag-unavailable": "Not tagged, the tagging engine is missing or blocked on this PC",
  "tag-ran-on-unknown-route": "Mixed content, check every part got tagged",
  "ocr-unavailable": "Text recognition is not installed on this PC",
  "ocr-failed": "Text recognition failed on this document",
  "ocr-timeout": "Text recognition took too long and was stopped",
  "ocr-skipped-unreadable": "No text recognition, the file is unreadable",
  "unreadable-encrypted-password-required": "Password-protected, cannot be processed",
  "decide-move-failed-file-in-use":
    "Decision recorded, but the file could not be moved to its folder because " +
    "another program had it open. Close it (e.g. Acrobat) and decide again.",
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
  // an edit made with the check switched off invalidates the old verdict:
  // never show a stale pass/fail as if it described the current file.
  if (item.verapdf_stale) return chip("changed since the last check", "flag");
  if (item.verapdf_passed === true) return chip("auto-check: passed", "pass");
  if (item.verapdf_passed === false) {
    const n = (item.verapdf_failed_clauses || []).length;
    return chip(`auto-check: ${n || "some"} issue${n === 1 ? "" : "s"}`, "fail");
  }
  return chip("not checked yet");
}

// ------------------------------------------------------------------- queue
// The sidebar is a tree of module groups: the typed module is pinned to the
// top and expanded, every other module is a collapsed one-line header, and
// each group's decided documents live in a collapsed "Done" section -- so the
// list never piles up as partners keep processing.

function queueRow(item) {
  const div = document.createElement("div");
  div.className =
    "qitem" +
    (selected === item.file ? " selected" : "") +
    (hiddenDocs.has(item.file) ? " is-hidden" : "");
  div.innerHTML =
    `<div class="name">${esc(item.name || item.file)}</div>` +
    `<div class="sub">${chip(STATUS_TEXT[item.status] || item.status, item.status)} ` +
    `${veraChip(item)}` +
    (item.output_changed === true ? ` ${chip("edited", "edited")}` : "") +
    (item.flags.length ? ` ${chip(item.flags.length + " note(s)", "flag")}` : "") +
    `</div>`;
  // remove from the panel: a view filter, never a delete -- the draft, the
  // approved copy, the record and the audit trail all stay exactly as they are.
  const close = document.createElement("button");
  close.className = "qclose";
  const isHidden = hiddenDocs.has(item.file);
  close.textContent = isHidden ? "↩" : "×";
  close.title = isHidden
    ? "Put this document back in the list"
    : "Remove from this list (the files are kept)";
  close.setAttribute("aria-label", close.title);
  close.onclick = (e) => {
    e.stopPropagation();
    if (isHidden) hiddenDocs.delete(item.file);
    else hiddenDocs.add(item.file);
    saveHidden();
    renderQueue();
  };
  div.appendChild(close);
  div.onclick = () => select(item.file);
  // the queue must work without a mouse: focusable, named, Enter/Space opens
  div.tabIndex = 0;
  div.setAttribute("role", "button");
  div.setAttribute("aria-label", `Open ${item.name || item.file} for review`);
  if (selected === item.file) div.setAttribute("aria-current", "true");
  div.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      select(item.file);
    }
  };
  return div;
}

function sectionToggle(text, cls, expanded, onToggle) {
  const head = document.createElement("div");
  head.className = cls;
  head.innerHTML = `<span class="caret">${expanded ? "▾" : "▸"}</span>${text}`;
  head.tabIndex = 0;
  head.setAttribute("role", "button");
  head.setAttribute("aria-expanded", String(expanded));
  head.onclick = onToggle;
  head.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  };
  return head;
}

function renderQueue() {
  const box = $("queue");
  box.innerHTML = "";
  const code = moduleCode.trim();
  // the module you are working IN can never be hidden: otherwise Add PDFs
  // lands files you cannot see and Process reports "no PDFs" while its inbox
  // is full. Typing a module un-removes it.
  if (code && hiddenModules.has(code)) {
    hiddenModules.delete(code);
    saveHidden();
  }

  // group everything (pending + processed) by module
  const groups = new Map(); // "" = the legacy flat root
  const ensure = (m) => {
    const key = m || "";
    if (!groups.has(key)) groups.set(key, { pending: [], review: [], done: [] });
    return groups.get(key);
  };
  // a document (or a whole module) the reviewer removed from the panel is
  // filtered out here -- a view filter only; every file stays on disk.
  const isHidden = (module, relid) =>
    !showHidden && (hiddenDocs.has(relid) || hiddenModules.has(module || ""));
  for (const relid of pending) {
    const [m, name] = splitId(relid);
    if (isHidden(m, relid)) continue;
    ensure(m).pending.push({ id: relid, name });
  }
  for (const item of queue) {
    if (isHidden(item.module, item.file)) continue;
    const g = ensure(item.module);
    (item.status === "approved" || item.status === "rejected" ? g.done : g.review).push(item);
  }
  if (code) ensure(code); // the typed module always shows, even when empty

  // typed module first, then the rest alphabetically, the flat root last
  const keys = [...groups.keys()].sort((a, b) => {
    if (a === code) return -1;
    if (b === code) return 1;
    if (!a) return 1;
    if (!b) return -1;
    return a.localeCompare(b);
  });

  for (const key of keys) {
    const g = groups.get(key);
    // newest processed/edited first -- the freshest work is at the top
    g.review.sort((x, y) => (y.updated_at || 0) - (x.updated_at || 0));
    g.done.sort((x, y) => (y.updated_at || 0) - (x.updated_at || 0));
    const isTyped = key === code && code !== "";
    const expanded =
      (collapsedOverride.has(key) ? !collapsedOverride.get(key) : isTyped) &&
      !hiddenModules.has(key); // a revealed-but-removed module stays folded
    const counts = [];
    if (g.pending.length) counts.push(`${g.pending.length} waiting`);
    counts.push(`${g.review.length} to review`);
    if (g.done.length) counts.push(`${g.done.length} done`);
    const title = `<span class="mname">${key || "(no module)"}</span>` +
      `<span class="mcounts">${counts.join(" · ")}</span>`;
    const head = sectionToggle(
      title,
      "mgroup-head" + (isTyped ? " current" : ""),
      expanded,
      () => {
        collapsedOverride.set(key, expanded); // toggle: remember the user's choice
        renderQueue();
      }
    );
    // remove the WHOLE module from the panel (files untouched, restorable).
    // While "Show" is on, a removed module offers the reverse instead, so a
    // single module can come back without restoring every other one.
    const moduleHidden = hiddenModules.has(key);
    const hideAll = document.createElement("button");
    hideAll.className = "qclose";
    hideAll.textContent = moduleHidden ? "↩" : "×";
    hideAll.title = moduleHidden
      ? `Put ${key || "these documents"} back in the list`
      : `Remove ${key || "these documents"} from this list (the files are kept)`;
    hideAll.setAttribute("aria-label", hideAll.title);
    hideAll.onclick = (e) => {
      e.stopPropagation();
      if (moduleHidden) hiddenModules.delete(key);
      else hiddenModules.add(key);
      saveHidden();
      renderQueue();
    };
    head.appendChild(hideAll);
    box.appendChild(head);
    if (!expanded) continue;

    for (const p of g.pending) {
      const div = document.createElement("div");
      div.className = "qitem waiting";
      // flat-root leftovers CANNOT be processed from the app (Process always
      // works per module): say what to actually do instead of lying.
      const hint = key
        ? chip("added, press Process", "draft")
        : chip("no module - move it into a module folder in the input folder", "flag");
      div.innerHTML = `<div class="name">${esc(p.name)}</div><div class="sub">${hint}</div>`;
      box.appendChild(div);
    }
    for (const item of g.review) box.appendChild(queueRow(item));
    if (!g.pending.length && !g.review.length && !g.done.length) {
      const empty = document.createElement("div");
      empty.className = "qitem muted";
      empty.textContent = "No documents yet - press Add PDFs.";
      box.appendChild(empty);
    }
    if (g.done.length) {
      const open = doneOpen.has(key);
      box.appendChild(
        sectionToggle(`Done (${g.done.length})`, "mdone-head", open, () => {
          if (open) doneOpen.delete(key);
          else doneOpen.add(key);
          renderQueue();
        })
      );
      if (open) for (const item of g.done) box.appendChild(queueRow(item));
    }
  }

  if (!queue.length && !pending.length && !code) {
    box.innerHTML =
      '<div class="qitem muted">No documents yet. Type your module code above, ' +
      "then Add PDFs and press Process.</div>";
  }

  // anything removed from the panel is one click from coming back -- nothing
  // was deleted, so the reviewer is never stranded.
  if (hiddenDocs.size || hiddenModules.size) {
    const total = hiddenDocs.size + hiddenModules.size;
    const restore = document.createElement("div");
    restore.className = "qrestore";
    restore.innerHTML = showHidden
      ? "<b>Showing removed documents</b>"
      : `${total} removed from this list`;
    const toggle = document.createElement("button");
    toggle.textContent = showHidden ? "Hide again" : "Show";
    toggle.onclick = () => {
      showHidden = !showHidden;
      renderQueue();
    };
    const clear = document.createElement("button");
    clear.textContent = "Put all back";
    clear.onclick = () => {
      hiddenDocs.clear();
      hiddenModules.clear();
      showHidden = false;
      saveHidden();
      renderQueue();
    };
    restore.append(toggle, clear);
    box.appendChild(restore);
  }

  // Add / Process work per module: both wait for a module code.
  const myPending = code
    ? (groups.get(code) ? groups.get(code).pending.length : 0)
    : 0;
  $("run").textContent = myPending
    ? `Process ${myPending} PDF${myPending === 1 ? "" : "s"} (${code})`
    : "Process PDFs";
  $("run").disabled = !code || batchRunning; // never re-arm mid-batch
  $("run").title = code
    ? `Process ${code}'s waiting PDFs`
    : "Type your module code first";
  $("add").disabled = !code;
  $("add").title = code
    ? `Add PDFs to ${code}`
    : "Type your module code first";
}

async function refreshModules() {
  knownModules = await api.modules().catch(() => []);
  const datalist = $("module-list");
  datalist.innerHTML = "";
  for (const m of knownModules) {
    const opt = document.createElement("option");
    opt.value = m;
    datalist.appendChild(opt);
  }
}

// `skipDetail`: the caller is mid-edit and will refresh the detail pane itself
// (cheaply). Re-rendering it here would re-fetch the whole tag tree a second
// time for the same edit -- the double load that made editing feel slow.
async function refresh(keepSelection = true, { skipDetail = false } = {}) {
  [queue, pending] = await Promise.all([api.listQueue(), api.listPending()]);
  refreshModules(); // keep the module suggestions current (fire-and-forget)
  if (!keepSelection || !queue.some((q) => q.file === selected)) selected = null;
  renderQueue();
  if (selected) {
    if (!skipDetail) renderDetail();
  } else {
    $("detail-body").hidden = true;
    $("detail-empty").hidden = false;
  }
}

// Where the reviewer is looking, so an edit does not throw them back to the
// top of the document (the complaint after tagging a dragged section).
function captureView() {
  return {
    page: $("page-scroll").scrollTop,
    tree: $("tag-tree").scrollTop,
    detail: $("detail").scrollTop,
  };
}

function restoreView(view) {
  if (!view) return;
  $("page-scroll").scrollTop = view.page;
  $("tag-tree").scrollTop = view.tree;
  $("detail").scrollTop = view.detail;
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
    return "Already had accessibility tags, kept them (standards metadata topped up)";
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

// One line per issue, ALWAYS as "code - what it means" (e.g. "7.3.2-1 - images
// missing descriptions"), so the code and its explanation never separate --
// the code is what Acrobat/IT conversations and docs/verapdf-clauses.md key on.
function veraIssueLines(clauses) {
  const groups = new Map(); // plain-language label -> the codes behind it
  for (const clause of clauses) {
    const hits = CLAUSE_TEXT.filter(([prefix]) => clause.startsWith(prefix));
    hits.sort((a, b) => b[0].length - a[0].length); // most specific match wins
    const label = hits.length ? hits[0][1] : "see docs/verapdf-clauses.md";
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(clause);
  }
  return [...groups].map(([label, codes]) => `${codes.join(", ")} – ${label}`);
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

// The facts block (what happened, the check verdict, flags, edited banner,
// undo depth). Split out of renderDetail so an edit can refresh JUST this --
// re-running the whole detail pane meant re-fetching the tag tree twice per
// keystroke-sized change, which is most of why editing felt slow.
function renderFacts(item) {
  const clauses = item.verapdf_failed_clauses || [];
  const issueText = clauses.length
    ? `${clauses.length} issue${clauses.length === 1 ? "" : "s"} - ` +
      "fix in the app or Acrobat, or use your judgement:" +
      veraIssueLines(clauses)
        .map((line) => `<br><span class="issue-line">${line}</span>`)
        .join("")
    : "found issues";
  const checkText = item.verapdf_stale
    ? "not checked since your last change - press <b>Check accessibility</b> " +
      "(your decision at the end always runs it)"
    : item.verapdf_passed === null
      ? "runs after processing and again when you decide"
      : item.verapdf_passed
        ? "passed (PDF/UA accessibility rules)"
        : issueText;
  $("facts").innerHTML =
    `<dt>Processed</dt><dd>${processedText(item)}</dd>` +
    `<dt>Automatic check</dt><dd>${checkText}</dd>` +
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
}

async function renderDetail() {
  const item = queue.find((q) => q.file === selected);
  if (!item) return;
  $("detail-empty").hidden = true;
  $("detail-body").hidden = false;
  $("doc-name").textContent = item.module
    ? `${item.module} / ${item.name}`
    : item.name || item.file;
  $("gate-result").textContent = "";
  renderFacts(item);

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
  // a structure reload invalidates the stepper's node list: close it silently
  // (its saves are already in the file; this reload is about to show them).
  altWalk = null;
  $("alt-modal").hidden = true;
  $("alt-walk").hidden = true;
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
      '<div class="muted">No tags yet - this document has no accessibility structure.' +
      ((tree.untagged || []).length
        ? " Drag a rectangle over content on the pages to start tagging it."
        : "") +
      "</div>";
  } else {
    renderTree();
  }
  renderPageStack(file);
  updateAltWalkButton();
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

// every individual line any tag owns, grouped by page -- the carve tool's grain.
function collectLines(nodes) {
  for (const node of nodes) {
    if (isHiddenLink(node)) continue;
    for (const line of node.lines || []) {
      (linesByPage[line.page] ||= []).push({ node, mcid: line.mcid, box: line.box, page: line.page });
    }
    collectLines(node.kids);
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
  div.onclick = () => {
    if (dragJustEnded) return; // the tail of a drag, not a click on this box
    selectNode(node, { scrollTree: true });
  };
  node.__box = div; // the marquee highlights selections through this
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
  clearBulkSelection();
  boxesByPage = [];
  linesByPage = [];
  untaggedByPage = [];
  if (structTree.tagged) {
    collectLeafBoxes(structTree.root, boxesByPage);
    collectLines(structTree.root);
  }
  for (const piece of structTree.untagged || []) {
    (untaggedByPage[piece.page] ||= []).push(piece);
  }
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
    band.addEventListener("mousedown", (e) => startMarquee(e, band, i, size));
    stack.appendChild(band);
    bands.push(band);
  });
  // pages render lazily as they scroll into view -- a 100-page scan must not
  // fetch 100 images up front. Sizes are known, so layout never jumps.
  // Bands stay OBSERVED after loading: scrolled-past pages are evicted once
  // the cache passes its cap (below), so a long scan cannot pin hundreds of
  // multi-MB data URIs and starve the WebView2 renderer (the one real leak
  // the crash investigation confirmed; the OS had logged a pre-leak warning).
  loadedBands = [];
  pageObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const band = entry.target;
        band.__visible = entry.isIntersecting;
        if (!entry.isIntersecting) continue;
        if (band.querySelector("img")) {
          // already cached: refresh its LRU position
          loadedBands = loadedBands.filter((b) => b !== band);
          loadedBands.push(band);
          continue;
        }
        loadBandImage(band, file);
      }
    },
    { root: $("page-scroll"), rootMargin: "600px" }
  );
  bands.forEach((b) => pageObserver.observe(b));
}

// One band's page render: shared by the scroll observer and the alt-text
// stepper's force-load, so both fill the band the same way exactly once.
function loadBandImage(band, file) {
  const index = parseInt(band.dataset.page, 10);
  api.pageImage(file, index).then((result) => {
    if (selected !== file || result.error || band.querySelector("img")) return;
    const img = document.createElement("img");
    img.alt = `Page ${index + 1}`;
    img.src = result.data_uri;
    img.draggable = false; // the mouse draws marquees here, not image drags
    band.prepend(img);
    loadedBands.push(band);
    evictPageImages();
  });
}

// ~2-2.5MB per rendered scan page: 24 cached bands ≈ 60MB renderer-side, a
// comfortable ceiling. Only off-screen bands are evicted (oldest first); a
// re-visit simply refetches -- pages re-render in a few hundred ms.
const PAGE_CACHE_LIMIT = 24;

function evictPageImages() {
  while (loadedBands.length > PAGE_CACHE_LIMIT) {
    const oldest = loadedBands.find((b) => !b.__visible);
    if (!oldest) return; // everything cached is on/near screen: keep it all
    loadedBands = loadedBands.filter((b) => b !== oldest);
    const img = oldest.querySelector("img");
    if (img) img.remove(); // the data URI is dropped; the observer refetches on return
  }
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
      // keyboard: one Tab stop for the whole tree (roving tabindex), arrows
      // move between rows, Enter/Space opens the tag in the editor
      row.tabIndex = -1;
      row.setAttribute("role", "button");
      row.setAttribute(
        "aria-label",
        typeText(node.type) + (text.textContent ? `: ${text.textContent}` : "")
      );
      row.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectNode(node);
        } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const rows = [...box.querySelectorAll(".tnode")].filter((r) => r.offsetParent);
          const at = rows.indexOf(row);
          const next = rows[at + (e.key === "ArrowDown" ? 1 : -1)];
          if (next) {
            row.tabIndex = -1;
            next.tabIndex = 0;
            next.focus();
          }
        }
      };
    }
  };
  build(structTree.root, box);
  const firstRow = box.querySelector(".tnode");
  if (firstRow) firstRow.tabIndex = 0; // the tree's single Tab entry point
  if (structTree.truncated) {
    const more = document.createElement("div");
    more.className = "muted";
    more.textContent = "…tree shortened (very large document)";
    box.appendChild(more);
  }
}

// ---------------------------------------------------- marquee (drag) selection
// Acrobat-style: drag a rectangle on a page and every tag box inside it is
// selected, then the bar above the pages offers one bulk action for the lot --
// merge into one tag, retag all, or mark all decorative. One drag = one saved
// operation = one undo step. The marquee lives on a single page band: reading
// order across pages is what the tree is for.

function startMarquee(e, band, pageIndex, size) {
  // untagged documents still get the marquee: dragging is how they get tags back
  if (e.button !== 0 || !structTree || editBusy) return;
  if (!structTree.tagged && !(structTree.untagged || []).length) return;
  e.preventDefault(); // no native image drag, no text selection
  const rect = band.getBoundingClientRect();
  const x0 = e.clientX - rect.left;
  const y0 = e.clientY - rect.top;
  let dragged = false;
  const marquee = document.createElement("div");
  marquee.className = "marquee";

  const shape = (ev) => {
    const x1 = Math.min(Math.max(ev.clientX - rect.left, 0), rect.width);
    const y1 = Math.min(Math.max(ev.clientY - rect.top, 0), rect.height);
    marquee.style.left = `${Math.min(x0, x1)}px`;
    marquee.style.top = `${Math.min(y0, y1)}px`;
    marquee.style.width = `${Math.abs(x1 - x0)}px`;
    marquee.style.height = `${Math.abs(y1 - y0)}px`;
    return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
  };

  const onMove = (ev) => {
    if (!dragged && Math.hypot(ev.clientX - rect.left - x0, ev.clientY - rect.top - y0) < 5) {
      return; // still a click, not a drag
    }
    if (!dragged) {
      dragged = true;
      band.appendChild(marquee);
    }
    shape(ev);
  };

  const onUp = (ev) => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    if (!dragged) return; // a plain click: the box's own onclick handles it
    const [px0, py0, px1, py1] = shape(ev);
    marquee.remove();
    // one drag must not ALSO fire the click of whatever box it ended on
    dragJustEnded = true;
    setTimeout(() => (dragJustEnded = false), 0); // cleared after the click lands
    // CSS pixels -> PDF points (y flips: PDF measures from the bottom)
    const sel = [
      (px0 / rect.width) * size.width,
      size.height - (py1 / rect.height) * size.height,
      (px1 / rect.width) * size.width,
      size.height - (py0 / rect.height) * size.height,
    ];
    setBulkSelection(
      (boxesByPage[pageIndex] || []).filter((node) => boxCovered(node.box, sel)),
      (linesByPage[pageIndex] || []).filter((line) => boxCovered(line.box, sel, 0.5)),
      (untaggedByPage[pageIndex] || []).filter((piece) => boxCovered(piece.box, sel, 0.5))
    );
  };

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

// selected when at least `need` of the box's area falls inside the rectangle:
// a sloppy drag still catches whole lines without grabbing barely-touched ones.
function boxCovered(box, sel, need = 0.4) {
  const w = Math.min(box[2], sel[2]) - Math.max(box[0], sel[0]);
  const h = Math.min(box[3], sel[3]) - Math.max(box[1], sel[1]);
  if (w <= 0 || h <= 0) return false;
  const area = (box[2] - box[0]) * (box[3] - box[1]);
  return area <= 0 || (w * h) / area >= need;
}

function setBulkSelection(nodes, lines, loose) {
  clearBulkSelection();
  lines = lines || [];
  loose = loose || [];
  // a drag that amounts to "this one whole tag" opens the ordinary editor
  const wholeSingle =
    nodes.length === 1 &&
    loose.length === 0 &&
    lines.every((l) => l.node.id === nodes[0].id) &&
    lines.length >= (nodes[0].lines || []).length;
  if (wholeSingle || (nodes.length === 0 && lines.length === 0 && loose.length === 0)) {
    if (nodes.length === 1) selectNode(nodes[0], { scrollTree: true });
    return;
  }
  bulkSelected = nodes.length >= 2 ? nodes : [];
  bulkLines = lines;
  bulkUntagged = loose;
  for (const node of bulkSelected) {
    if (node.__box) node.__box.classList.add("bulksel");
    if (node.__row) node.__row.classList.add("bulksel");
  }
  // individual highlights show EXACTLY what an action would take: yellow for
  // tagged lines (carve), dashed red for content that has no tag yet
  for (const [list, cls] of [
    [lines, "linesel"],
    [loose, "unsel"],
  ]) {
    for (const piece of list) {
      const band = $("page-stack").querySelector(`.pageband[data-page="${piece.page}"]`);
      if (!band || !structTree) continue;
      const mark = document.createElement("div");
      mark.className = cls;
      boxStyle(mark, piece.box, structTree.pages[piece.page]);
      band.appendChild(mark);
    }
  }
  const parts = [];
  if (bulkSelected.length >= 2) parts.push(`${bulkSelected.length} tags`);
  if (lines.length) parts.push(`${lines.length} line${lines.length === 1 ? "" : "s"}`);
  if (loose.length) parts.push(`${loose.length} untagged`);
  $("bulk-count").textContent = parts.join(" · ") + " selected";

  // A drag that creates a NEW tag (untagged content, or lines carved out of a
  // tag) needs no button: it becomes the type chosen in the bar, immediately.
  // A drag across two or more WHOLE tags still asks, because "merge into one"
  // and "change each" are genuinely different outcomes.
  if (bulkSelected.length < 2 && (loose.length || lines.length)) {
    runBulk(loose.length ? "tagnew" : "carve");
    return;
  }
  if (bulkSelected.length >= 2 && (loose.length || lines.length)) {
    // the bar acts on whole tags only: stop highlighting parts it cannot touch,
    // and say so rather than leaving a promise the buttons do not keep.
    document.querySelectorAll(".linesel, .unsel").forEach((el) => el.remove());
    bulkLines = [];
    bulkUntagged = [];
    $("bulk-count").textContent =
      `${bulkSelected.length} tags selected · untagged content in the rectangle ` +
      "was left alone (drag it on its own to tag it)";
  }
  $("bulk-actions").hidden = bulkSelected.length < 2;
}

function fillTagTypes() {
  const select = $("bulk-type");
  if (select.options.length) return;
  for (const t of tagTypes) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = `${typeText(t)} (${t})`;
    select.appendChild(opt);
  }
  select.value = "P";
}

function clearBulkSelection() {
  bulkSelected = [];
  bulkLines = [];
  bulkUntagged = [];
  $("bulk-actions").hidden = true;
  $("bulk-count").textContent = "drag over anything the app missed";
  document.querySelectorAll(".bulksel").forEach((el) => el.classList.remove("bulksel"));
  document.querySelectorAll(".linesel, .unsel").forEach((el) => el.remove());
}

async function runBulk(action) {
  const armed = {
    carve: bulkLines.length > 0,
    tagnew: bulkUntagged.length > 0,
    merge: bulkSelected.length >= 2,
    retag: bulkSelected.length >= 2,
    decorative: bulkSelected.length >= 2,
  }[action];
  if (!armed || editBusy) return; // one edit at a time: ids shift underneath
  const n = { carve: bulkLines.length, tagnew: bulkUntagged.length }[action] || bulkSelected.length;
  const newType = $("bulk-type").value || "P";
  if (action === "decorative") {
    const withText = bulkSelected.filter((s) => s.text && s.text.trim().length > 3).length;
    if (
      withText &&
      !window.confirm(
        `${withText} of the ${n} selected tags contain text. Marking them decorative ` +
          "removes them from the reading order, so a screen reader will not read " +
          "them out. Do that only for decoration.\n\nContinue?"
      )
    ) {
      return;
    }
  }
  const buttons = ["bulk-merge", "bulk-retag", "bulk-deco"];
  setEditBusy(true);
  for (const id of buttons) $(id).disabled = true;
  setStatus("Applying to the selection…");
  let result;
  if (action === "carve") {
    result = await api.carveTag(
      selected,
      bulkLines.map((l) => ({ node_id: l.node.id, mcid: l.mcid })),
      newType
    );
  } else if (action === "tagnew") {
    result = await api.tagUntagged(
      selected,
      bulkUntagged[0].page,
      bulkUntagged.map((p) => p.index),
      newType
    );
  } else {
    result = await api.bulkEdit(selected, action, bulkSelected.map((s) => s.id), newType);
  }
  for (const id of buttons) $(id).disabled = false;
  setEditBusy(false);
  if (result.error) {
    setStatus(result.error);
    return;
  }
  const what = {
    merge: `${n} tags merged into one ${typeText(newType)}`,
    retag: `${n} tags changed to ${typeText(newType)}`,
    decorative: `${n} tags marked decorative`,
    carve: `${n} line${n === 1 ? "" : "s"} carved into a new ${typeText(newType)}`,
    tagnew: `${n} untagged piece${n === 1 ? "" : "s"} tagged as ${typeText(newType)}`,
  }[action];
  setStatus(`${what} - ${verdictText(result)}. One "Undo last change" reverses all of it.`);
  const file = selected;
  const view = captureView();
  await refresh(true, { skipDetail: true });
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  await loadStructure(file); // ids shift: the tree must be re-read
  restoreView(view); // ...but the reviewer stays on the section they tagged
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
  // "Remove tag, keep contents" only works on WRAPPERS (the backend refuses a
  // tag that holds content directly) -- so only show it where it applies,
  // instead of making the reviewer choose between two remove-ish buttons.
  $("unwrap-tag").hidden = (node.lines || []).length > 0;
}

// What to say about the accessibility check after an edit. With the check
// switched off there IS no verdict -- say so plainly rather than repeating a
// verdict that predates the change.
function verdictText(result) {
  if (result.checked === false) {
    return "not checked yet (press Check accessibility when you are ready)";
  }
  return result.verapdf_passed
    ? "automatic check now passes"
    : `automatic check: ${(result.failed_clauses || []).length} issue(s) left`;
}

// `opts.treeUnchanged`: the edit altered a tag's own properties (its type, its
// description) without adding or removing elements, so every node id still
// resolves and the expensive tree+pages rebuild can be skipped entirely.
async function afterEdit(result, message, opts = {}) {
  if (result.error) {
    $("editor-result").textContent = result.error;
    return;
  }
  $("editor-result").textContent = `${message} - ${verdictText(result)}.`;
  const file = selected;
  const nodeId = selectedNode ? selectedNode.id : null;
  const view = captureView();
  await refresh(true, { skipDetail: true }); // queue chips + banner, no tree
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  if (opts.treeUnchanged) {
    // the tree object is still valid, but the derived header counter is not
    updateAltWalkButton();
    restoreView(view);
    return;
  }
  await loadStructure(file); // rebuild the tree from the changed PDF
  restoreView(view); // ...and put the reviewer back where they were
  const again = findNodeById(structTree && structTree.root, nodeId);
  if (again) selectNode(again, { scrollTree: true });
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
  const node = selectedNode;
  const newType = $("tag-type").value;
  if (!newType || newType === node.type || editBusy) return;
  setEditBusy(true);
  $("save-tag-type").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.setTagType(selected, node.id, newType);
  $("save-tag-type").disabled = false;
  setEditBusy(false);
  if (!result.error) {
    // a retag swaps one element's type: same tree, same ids -> patch in place
    node.type = newType;
    updateFigureRow(node);
    showEditor(node); // the editor's own labels follow the new type
  }
  await afterEdit(result, `Changed to ${typeText(newType)}`, { treeUnchanged: true });
}

async function saveAlt() {
  if (!selectedNode) return;
  const node = selectedNode;
  const text = $("figure-alt").value.trim();
  if (editBusy) return;
  setEditBusy(true);
  $("save-alt").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.setFigureAlt(selected, node.id, $("figure-alt").value);
  $("save-alt").disabled = false;
  setEditBusy(false);
  if (!result.error) {
    // /Alt lives on the element: same tree, same ids -> patch in place
    node.alt = text || null;
    updateFigureRow(node);
  }
  await afterEdit(result, text ? "Description saved" : "Description cleared", {
    treeUnchanged: true,
  });
}

async function unwrapTag() {
  if (!selectedNode) return;
  const what = typeText(selectedNode.type);
  if (editBusy) return;
  setEditBusy(true);
  $("unwrap-tag").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.unwrapTag(selected, selectedNode.id);
  $("unwrap-tag").disabled = false;
  setEditBusy(false);
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
        "page numbers, background scans).\n\nTo bring it back later: Undo last " +
        "change, or drag over it again to tag it.\n\nContinue?"
    );
    if (!ok) return;
  }
  if (editBusy) return;
  setEditBusy(true);
  $("make-decorative").disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.makeDecorative(selected, selectedNode.id);
  $("make-decorative").disabled = false;
  setEditBusy(false);
  // the tag is gone from the tree, so there is nothing to re-select afterwards
  selectedNode = null;
  await afterEdit(result, `${what} is now decoration (no description needed)`);
  $("tag-editor").hidden = true;
}

// ------------------------------------------- set-alternate-text stepper
// Acrobat's "Set Alternate Text" workflow: one floating dialog that walks
// through EVERY image in the document (prev/next), with the page behind
// scrolling to each one. Each arrow press saves the current image first (the
// app's one-edit-one-undo-one-recheck convention), so nothing typed is lost.

function collectFigures(nodes, out = []) {
  for (const node of nodes) {
    if (node.type === "Figure" || node.type === "Formula") out.push(node);
    collectFigures(node.kids, out);
  }
  return out;
}

function updateAltWalkButton() {
  const figures = structTree && structTree.tagged ? collectFigures(structTree.root) : [];
  const need = figures.filter((f) => !f.alt).length;
  $("alt-walk").hidden = figures.length === 0;
  $("alt-walk").textContent = need ? `Alt Text (${need} needed)` : "Alt Text";
}

function openAltWalk() {
  if (!structTree || !structTree.tagged) return;
  const figures = collectFigures(structTree.root);
  if (!figures.length) return;
  const firstMissing = figures.findIndex((f) => !f.alt);
  altWalk = { figures, at: firstMissing === -1 ? 0 : firstMissing, savedAny: false };
  $("alt-modal").hidden = false;
  showAltFigure();
}

function showAltFigure() {
  const node = altWalk.figures[altWalk.at];
  const noun = node.type === "Formula" ? "Formula" : "Image";
  $("alt-count").textContent = `${noun} ${altWalk.at + 1} of ${altWalk.figures.length}`;
  $("alt-prev").disabled = altWalk.at === 0;
  $("alt-next").disabled = altWalk.at === altWalk.figures.length - 1;
  $("alt-decorative").checked = false;
  $("alt-text").disabled = false;
  $("alt-text").value = node.alt || "";
  // a save message from the arrow press that got us here survives the move
  const carried = altWalk.carryStatus;
  altWalk.carryStatus = null;
  $("alt-status").textContent = carried || (node.alt ? "" : "No description yet.");
  // the Acrobat behavior: every arrow press brings THE image being described
  // into view. selectNode without scrollTree scrolls the PAGE to the figure
  // (scrollTree:true would mean "the click came from the page: tree only").
  selectNode(node);
  if (node.__row) node.__row.scrollIntoView({ block: "nearest", behavior: SCROLL });
  // lazy loading is not enough here: fetch the page render NOW, so the
  // reviewer never describes a blank band (and an evicted page comes back).
  ensurePageImage(node.page);
  // keep the dialog off the image: figure sits in the right half of its page
  // -> dialog docks left, and vice versa.
  if (node.box && node.page !== null && structTree) {
    const size = structTree.pages[node.page];
    const centerFrac = (node.box[0] + node.box[2]) / 2 / size.width;
    $("alt-modal").classList.toggle("dodge-left", centerFrac > 0.5);
  }
  $("alt-text").focus();
}

// Fetch one band's page render immediately (the stepper's force-load): the
// IntersectionObserver only fires once scrolling settles, and the LRU may
// have evicted a previously seen page.
function ensurePageImage(pageIndex) {
  if (pageIndex === null || pageIndex === undefined || !structTree) return;
  const band = $("page-stack").querySelector(`.pageband[data-page="${pageIndex}"]`);
  if (!band) return;
  if (band.querySelector("img")) {
    loadedBands = loadedBands.filter((b) => b !== band); // LRU touch: it is
    loadedBands.push(band); // being looked at right now, evict it last
    return;
  }
  loadBandImage(band, selected);
}

function setAltWalkBusy(busy) {
  for (const id of ["alt-prev", "alt-next", "alt-save-close", "alt-cancel", "alt-close"]) {
    $(id).disabled = busy;
  }
  if (busy) $("alt-status").textContent = "Saving…";
}

// Saves the image on screen if it changed. Returns false when the save failed
// (the stepper stays put so nothing is silently dropped).
async function saveAltWalkCurrent() {
  const node = altWalk.figures[altWalk.at];
  const decorative = $("alt-decorative").checked;
  const text = $("alt-text").value.trim();
  if (!decorative && text === (node.alt || "").trim()) return true; // unchanged
  if (decorative && node.text && node.text.trim().length > 3) {
    const ok = window.confirm(
      `This ${typeText(node.type)} contains text:\n\n"${node.text.slice(0, 120)}"\n\n` +
        "Marking it decorative removes it from the reading order, so a screen " +
        "reader will not read it out. Do that only for decoration.\n\nContinue?"
    );
    if (!ok) return false;
  }
  setAltWalkBusy(true);
  const result = decorative
    ? await api.makeDecorative(selected, node.id)
    : await api.setFigureAlt(selected, node.id, text);
  setAltWalkBusy(false);
  if (result.error) {
    $("alt-status").textContent = result.error;
    return false;
  }
  altWalk.savedAny = true;
  const verdict = verdictText(result);
  if (decorative) {
    // the element is GONE (now an artifact) and every later tag id shifted:
    // the tree must be re-read before any further edit can be trusted.
    $("alt-status").textContent = altWalk.carryStatus = `Marked decorative - ${verdict}.`;
    await reloadAltWalkTree();
  } else {
    node.alt = text || null;
    updateFigureRow(node);
    $("alt-status").textContent = altWalk.carryStatus =
      `${text ? "Description saved" : "Description cleared"} - ${verdict}.`;
  }
  updateAltWalkButton();
  return true;
}

// After a decorative apply: re-fetch the tree, redraw, and rebuild the figure
// list. The removed figure's successor slides into its slot, so `at` is kept
// (clamped); with no figures left the stepper simply closes.
async function reloadAltWalkTree() {
  const file = selected;
  const tree = await api.structureTree(file);
  if (selected !== file || tree.error || !altWalk) {
    await closeAltWalk();
    return;
  }
  structTree = tree;
  renderTree();
  renderPageStack(file);
  const figures = collectFigures(tree.root);
  if (!figures.length) {
    await closeAltWalk();
    return;
  }
  altWalk.figures = figures;
  altWalk.at = Math.min(altWalk.at, figures.length - 1);
}

// An edit to a tag's own properties (its type, its description) updates the
// tree row and page box IN PLACE: the tree's shape is unchanged, so every node
// id stays valid and the expensive rebuild is skipped. This is what makes
// retagging and writing descriptions feel instant.
function updateFigureRow(node) {
  const row = node.__row;
  const figureLike = node.type === "Figure" || node.type === "Formula";
  if (row) {
    const type = row.querySelector(".ttype");
    if (type) type.textContent = typeText(node.type);
    const text = row.querySelector(".ttext");
    // mirror renderTree exactly: own text, else (figures only) the description
    // placeholder, else text borrowed from descendants. Without the last
    // branch a retagged wrapper row (an H2 whose text sits in a child Link, an
    // LI, a TR) would be relabelled "(no description yet)".
    if (text) {
      text.textContent =
        node.text ||
        (figureLike ? node.alt || "(no description yet)" : "") ||
        descendantText(node);
    }
    row.setAttribute(
      "aria-label",
      typeText(node.type) + (text && text.textContent ? `: ${text.textContent}` : "")
    );
    const flag = row.querySelector(".tflag");
    const needsAlt = figureLike && !node.alt;
    if (!needsAlt) {
      row.classList.remove("needs-attention");
      if (flag) flag.remove();
    } else if (!flag) {
      row.classList.add("needs-attention");
      const warn = document.createElement("span");
      warn.className = "tflag";
      warn.textContent = "needs description";
      row.appendChild(warn);
    }
  }
  if (node.__box) {
    const tag = node.__box.querySelector(".btag");
    if (tag) tag.textContent = node.type;
    node.__box.title = typeText(node.type) + (node.text ? ` - ${node.text}` : "");
  }
}

async function altWalkGo(delta) {
  if (!altWalk) return;
  if (altWalk.at + delta < 0 || altWalk.at + delta >= altWalk.figures.length) return;
  const before = altWalk.figures.length;
  if (!(await saveAltWalkCurrent())) return; // failed or cancelled: stay put
  if (!altWalk) return; // the save closed the stepper (last figure went decorative)
  if (altWalk.figures.length < before && delta > 0) {
    showAltFigure(); // decorative removed the current one: its successor is already here
    return;
  }
  altWalk.at = Math.min(Math.max(altWalk.at + delta, 0), altWalk.figures.length - 1);
  showAltFigure();
}

async function altWalkSaveClose() {
  if (!altWalk) return;
  if (!(await saveAltWalkCurrent())) return;
  await closeAltWalk();
}

// Cancel / x: the image on screen is NOT saved; descriptions saved while
// stepping stay in the file (each was its own audited, undoable edit).
async function closeAltWalk() {
  const refreshNeeded = altWalk && altWalk.savedAny;
  altWalk = null;
  $("alt-modal").hidden = true;
  if (refreshNeeded) {
    const file = selected;
    const view = captureView();
    await refresh(true, { skipDetail: true }); // chips + "edited" banner
    if (selected !== file) return;
    const item = queue.find((q) => q.file === file);
    if (item) renderFacts(item);
    await loadStructure(file);
    restoreView(view);
  }
}

async function moveTag(delta) {
  if (!selectedNode) return;
  if (editBusy) return;
  setEditBusy(true);
  const button = delta < 0 ? $("move-earlier") : $("move-later");
  button.disabled = true;
  $("editor-result").textContent = "Saving…";
  const result = await api.moveTag(selected, selectedNode.id, delta);
  button.disabled = false;
  setEditBusy(false);
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
  const view = captureView();
  await refresh(true, { skipDetail: true });
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  await loadStructure(file);
  restoreView(view);
}

async function checkNow() {
  if (!selected) return;
  const file = selected;
  $("check-now").disabled = true;
  $("check-now").textContent = "Checking…";
  setStatus("Running the accessibility check…");
  const result = await api.checkNow(file);
  $("check-now").disabled = false;
  $("check-now").textContent = "Check accessibility";
  if (result.error) {
    setStatus(result.error);
    return;
  }
  setStatus(
    result.verapdf_passed
      ? "Accessibility check passed."
      : `Accessibility check: ${(result.failed_clauses || []).length} issue(s) found.`
  );
  const view = captureView();
  await refresh(true, { skipDetail: true }); // the verdict + chips, no tree reload
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  restoreView(view);
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
  const view = captureView();
  await refresh(true, { skipDetail: true });
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  await loadStructure(file);
  restoreView(view);
}

async function revertDocument() {
  if (!selected) return;
  const file = selected;
  $("revert").disabled = true;
  setStatus("Undoing all edits: reprocessing the original document…");
  const result = await api.reprocess(file);
  $("revert").disabled = false;
  setStatus(result.error ? result.error : "Reprocessed from the original document.");
  await refresh(true, { skipDetail: true });
  if (selected !== file) return;
  const item = queue.find((q) => q.file === file);
  if (item) renderFacts(item);
  await loadStructure(file); // a fresh document: the top is the right place
}

function selectNode(node, opts = {}) {
  showEditor(node);
  if (structSelectedRow) {
    structSelectedRow.classList.remove("selected");
    structSelectedRow.removeAttribute("aria-current");
    structSelectedRow.tabIndex = -1;
  }
  structSelectedRow = node.__row || null;
  if (structSelectedRow) {
    structSelectedRow.classList.add("selected");
    structSelectedRow.setAttribute("aria-current", "true");
    structSelectedRow.tabIndex = 0; // Tab re-enters the tree at the selection
    if (opts.scrollTree) {
      structSelectedRow.scrollIntoView({ block: "nearest", behavior: SCROLL });
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
  if (!opts.scrollTree) emphasis.scrollIntoView({ block: "center", behavior: SCROLL });
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
    if (result.error) {
      $("gate-result").textContent = "Error: " + result.error;
      return;
    }
    const verdict = result.verapdf_passed
      ? "automatic check passed"
      : "automatic check found issues: " +
        (veraIssueLines(result.failed_clauses || []).join("; ") || "unlisted");
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

// A batch of ~50 is a comfortable amount to review in one sitting; past this we
// nudge the reviewer to work in smaller batches (never block — already-done
// files are skipped, so several small batches cost nothing).
const LARGE_BATCH = 100;

async function runBatch() {
  const code = moduleCode.trim();
  if (!code || batchRunning) return; // disabled buttons; belt and braces
  const myPending = pending.filter((relid) => splitId(relid)[0] === code).length;
  if (
    myPending > LARGE_BATCH &&
    !window.confirm(
      `You are about to process ${myPending} PDFs for ${code}. That is a lot to review ` +
        "in one sitting.\n\nYou can process them in smaller batches (around 50 at a " +
        "time) — already-processed files are skipped, so nothing is wasted by " +
        "splitting it up.\n\nProcess all " +
        myPending +
        " now anyway?"
    )
  ) {
    return; // they chose to trim the inbox and do a smaller batch
  }
  batchRunning = true;
  $("run").disabled = true;
  setStatus("");
  const started = await api.runBatchStart(code);
  if (started.error) {
    setStatus(started.error);
    batchRunning = false;
    renderQueue(); // restore the button state consistently
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
    batchRunning = false;
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
  const code = moduleCode.trim();
  if (!code) return; // the button is disabled without a module; belt and braces
  const result = await api.addPdfs(code);
  if (result.copied && result.copied.length) {
    // no status line listing the filenames: they appear in the sidebar under
    // their module immediately, and the Process button already counts them.
    setStatus("");
    await refresh();
  } else if (result.error) {
    setStatus(result.error);
  } else {
    // picker cancelled, or nothing usable picked: say so rather than nothing
    // (only PDF files are accepted; a .docx pick is silently skipped upstream).
    setStatus("Nothing was added - only PDF files can be added.");
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
  const file = esc((e.module ? `${e.module} / ` : "") + (e.file || "-"));
  let what;
  if (e.event === "run") {
    const steps = (e.stages_applied || []).map((s) => STAGE_TEXT[s] || s).join(", ");
    what = `processed (${steps || "no steps"})`;
  } else if (e.event === "verify") {
    what =
      `${STATUS_TEXT[e.decision] || e.decision} by ${esc(e.reviewer)}` +
      ` - automatic check ${e.verapdf_passed ? "passed" : "found issues"}`;
  } else if (EDIT_TEXT[e.event]) {
    what = EDIT_TEXT[e.event](e);
  } else {
    what = e.event;
  }
  return `<tr><td>${when}</td><td>${file}</td><td>${what}</td></tr>`;
}

async function exportHistory() {
  $("export-history").disabled = true;
  const result = await api.exportHistory();
  $("export-history").disabled = false;
  if (result.error) {
    setStatus(result.error);
    return;
  }
  setStatus(`History saved as ${result.name} in the output folder.`);
  api.openOutbox(); // show the file rather than describing where it went
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
  const moduleInput = $("module-code");
  moduleInput.value = moduleCode;
  moduleInput.oninput = () => {
    // Case handling, for real (CSS text-transform is display-only): an
    // EXISTING module keeps its exact folder casing -- typing or picking
    // "mg2001" when that folder exists must select it, not invent a second
    // "MG2001" (folders are case-sensitive on the Linux deploy target). Only
    // a brand-NEW code is normalised to uppercase.
    const caret = moduleInput.selectionStart;
    const typed = moduleInput.value.trim();
    const existing = knownModules.find((m) => m.toLowerCase() === typed.toLowerCase());
    moduleInput.value = existing || moduleInput.value.toUpperCase();
    moduleInput.setSelectionRange(caret, caret);
    moduleCode = moduleInput.value;
    try {
      localStorage.setItem("speade-module", moduleCode.trim());
    } catch (e) { /* storage unavailable: the box still works for this session */ }
    renderQueue();
  };
  $("save-meta").onclick = saveMeta;
  $("doc-lang").onchange = () => {
    $("doc-lang-other").hidden = $("doc-lang").value !== "__other";
    if (!$("doc-lang-other").hidden) $("doc-lang-other").focus();
  };
  $("refresh").onclick = () => refresh();
  $("add").onclick = addPdfs;
  $("open-inbox").onclick = () => api.openInbox(moduleCode.trim());
  $("open-outbox").onclick = () => api.openOutbox();
  $("approve").onclick = () => decide(true);
  $("reject").onclick = () => decide(false);
  $("history").onclick = toggleHistory;
  $("export-history").onclick = exportHistory;
  $("show-links").onchange = () => {
    showLinks = $("show-links").checked;
    if (structTree && structTree.tagged && selected) {
      renderTree();
      renderPageStack(selected);
    }
  };
  $("save-tag-type").onclick = saveTagType;
  $("save-alt").onclick = saveAlt;
  $("alt-walk").onclick = openAltWalk;
  $("alt-prev").onclick = () => altWalkGo(-1);
  $("alt-next").onclick = () => altWalkGo(1);
  $("alt-save-close").onclick = altWalkSaveClose;
  $("alt-cancel").onclick = closeAltWalk;
  $("alt-close").onclick = closeAltWalk;
  $("alt-decorative").onchange = () => {
    // decoration needs no description: grey the text out, like Acrobat
    $("alt-text").disabled = $("alt-decorative").checked;
  };
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
  fillTagTypes(); // the drag bar's type must be pickable BEFORE any drag
  $("check-now").onclick = checkNow;
  $("settings").onclick = () => ($("settings-overlay").hidden = false);
  $("settings-close").onclick = () => ($("settings-overlay").hidden = true);
  $("settings-overlay").onclick = (e) => {
    if (e.target === $("settings-overlay")) $("settings-overlay").hidden = true;
  };
  $("auto-check-toggle").checked = autoCheck;
  $("auto-check-toggle").onchange = async () => {
    autoCheck = $("auto-check-toggle").checked;
    try {
      localStorage.setItem("speade-auto-check", autoCheck ? "1" : "0");
    } catch (e) { /* storage unavailable: the setting holds for this session */ }
    await api.setAutoCheck(autoCheck);
    setStatus(
      autoCheck
        ? "The accessibility check will run after every change (slower)."
        : "The accessibility check now runs only when you press Check accessibility."
    );
  };
  await api.setAutoCheck(autoCheck); // the backend starts in sync with the box
  $("help").onclick = () => ($("help-overlay").hidden = false);
  $("help-close").onclick = () => ($("help-overlay").hidden = true);
  $("help-overlay").onclick = (e) => {
    if (e.target === $("help-overlay")) $("help-overlay").hidden = true;
  };
  $("bulk-merge").onclick = () => runBulk("merge");
  $("bulk-retag").onclick = () => runBulk("retag");
  $("bulk-deco").onclick = () => runBulk("decorative");
  $("bulk-clear").onclick = clearBulkSelection;
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    $("help-overlay").hidden = true;
    $("settings-overlay").hidden = true;
    if (altWalk && !$("alt-close").disabled) closeAltWalk(); // not mid-save
    clearBulkSelection();
  });

  // full paths live in the folder buttons' tooltips, not the header.
  const ws = await api.workspace();
  $("open-inbox").title = ws.inbox;
  $("open-outbox").title = ws.outbox;
  $("reviewer").value = await api.reviewerDefault();
  await refresh(false);
}

init();
