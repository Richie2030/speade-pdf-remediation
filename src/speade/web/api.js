// api.js -- THE single seam between the UI and its backend.
//
// Web delivery (this one): fetch() against the localhost FastAPI app
// (speade.web.server); every call below hits an /api/* endpoint on 127.0.0.1.
// The desktop delivery keeps its own api.js (the pywebview js_api bridge);
// app.js is identical in both and must not change.

async function getJSON(path) {
  const r = await fetch(path);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

const api = {
  workspace: () => getJSON("/api/workspace"),
  reviewerDefault: async () => (await getJSON("/api/reviewer_default")).reviewer,
  listQueue: () => getJSON("/api/list_queue"),
  listPending: () => getJSON("/api/list_pending"),

  // The browser fetches the PDF bytes and hands app.js the same {data_uri}
  // shape the desktop bridge returns, so app.js stays delivery-agnostic.
  loadPdf: async (file) => {
    const r = await fetch("/api/pdf?file=" + encodeURIComponent(file));
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      return { error: data.error || "Preview unavailable." };
    }
    const blob = await r.blob();
    const data_uri = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    return { data_uri };
  },

  runBatch: () => postJSON("/api/run_batch_start"),
  runBatchStart: (module) => postJSON("/api/run_batch_start", { module: module || null }),
  modules: async () => (await getJSON("/api/modules")).modules,
  autoCheck: async () => (await getJSON("/api/auto_check")).auto_check,
  setAutoCheck: (on) => postJSON("/api/auto_check", { on: !!on }),
  setCheckOnDecide: (on) => postJSON("/api/check_on_decide", { on: !!on }),
  checkNow: (file) => postJSON("/api/check_now", { file }),
  runBatchStatus: () => getJSON("/api/run_batch_status"),
  runBatchCancel: () => postJSON("/api/run_batch_cancel"),
  structure: (file) => getJSON("/api/structure?file=" + encodeURIComponent(file)),
  structureTree: (file) => getJSON("/api/structure_tree?file=" + encodeURIComponent(file)),
  tagTypes: () => getJSON("/api/tag_types"),
  setTagType: (file, nodeId, newType) =>
    postJSON("/api/tag_type", { file, node_id: nodeId, new_type: newType }),
  setFigureAlt: (file, nodeId, alt) =>
    postJSON("/api/figure_alt", { file, node_id: nodeId, alt }),
  makeDecorative: (file, nodeId) => postJSON("/api/decorative", { file, node_id: nodeId }),
  moveTag: (file, nodeId, delta) =>
    postJSON("/api/move_tag", { file, node_id: nodeId, delta }),
  bulkEdit: (file, action, nodeIds, newType) =>
    postJSON("/api/bulk_edit", {
      file,
      action,
      node_ids: nodeIds,
      new_type: newType || null,
    }),
  carveTag: (file, picks, newType) =>
    postJSON("/api/carve_tag", { file, picks, new_type: newType }),
  tagUntagged: (file, page, indexes, newType) =>
    postJSON("/api/tag_untagged", { file, page, indexes, new_type: newType }),
  unwrapTag: (file, nodeId) => postJSON("/api/unwrap_tag", { file, node_id: nodeId }),
  undoLastEdit: (file) => postJSON("/api/undo_last_edit", { file }),
  removeAllTags: (file) => postJSON("/api/remove_all_tags", { file }),
  exportHistory: () => postJSON("/api/export_history", {}),
  reprocess: (file) => postJSON("/api/reprocess", { file }),
  pageImage: (file, index) =>
    getJSON(
      "/api/page_image?file=" + encodeURIComponent(file) + "&index=" + (index || 0)
    ),
  docMetadata: (file) => getJSON("/api/metadata?file=" + encodeURIComponent(file)),
  setDocMetadata: (file, title, lang) => postJSON("/api/metadata", { file, title, lang }),
  auditLog: (limit) => getJSON("/api/audit_log?limit=" + (limit || 200)),
  decide: (file, reviewer, approve) => postJSON("/api/decide", { file, reviewer, approve }),
  openOutput: (file) => postJSON("/api/open_output", { file }).then((r) => r.ok),
  openErrorLog: () => postJSON("/api/open_error_log", {}),
  openInbox: (module) =>
    postJSON("/api/open_inbox", { module: module || null }).then((r) => r.ok),
  openOutbox: () => postJSON("/api/open_outbox").then((r) => r.ok),

  // The browser's stand-in for the native file dialog: a hidden picker whose
  // selection is uploaded multipart into the (module's) inbox.
  addPdfs: (module) =>
    new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".pdf,application/pdf";
      input.multiple = true;
      input.onchange = async () => {
        if (!input.files.length) return resolve({ copied: [] });
        const form = new FormData();
        form.append("module", module || "");
        for (const f of input.files) form.append("files", f, f.name);
        const r = await fetch("/api/upload", { method: "POST", body: form });
        resolve(r.ok ? await r.json() : { error: "Upload failed." });
      };
      input.oncancel = () => resolve({ copied: [] });
      input.click();
    }),
};
