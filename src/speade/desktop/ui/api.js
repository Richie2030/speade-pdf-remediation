// api.js -- THE single seam between the UI and its backend.
//
// Desktop delivery (this one): pywebview's js_api bridge -- every call below
// resolves to a Python method on speade.desktop.api.SpeadeApi.
// If a browser/web delivery ever returns (see docs/decisions/frontend-delivery.md),
// reimplement ONLY this file with fetch() against the backend; app.js must not change.

const bridgeReady = new Promise((resolve) => {
  if (window.pywebview) resolve();
  else window.addEventListener("pywebviewready", resolve);
});

async function bridge(method, ...args) {
  await bridgeReady;
  return window.pywebview.api[method](...args);
}

const api = {
  workspace: () => bridge("workspace"),
  reviewerDefault: () => bridge("reviewer_default"),
  listQueue: () => bridge("list_queue"),
  listPending: () => bridge("list_pending"),
  modules: () => bridge("modules"),
  autoCheck: () => bridge("auto_check"),
  setAutoCheck: (on) => bridge("set_auto_check", !!on),
  checkNow: (file) => bridge("check_now", file),
  loadPdf: (file) => bridge("load_pdf", file),
  runBatch: () => bridge("run_batch"),
  runBatchStart: (module) => bridge("run_batch_start", false, module || null),
  runBatchStatus: () => bridge("run_batch_status"),
  runBatchCancel: () => bridge("run_batch_cancel"),
  docMetadata: (file) => bridge("doc_metadata", file),
  setDocMetadata: (file, title, lang) => bridge("set_doc_metadata", file, title, lang),
  structure: (file) => bridge("structure", file),
  structureTree: (file) => bridge("structure_tree", file),
  tagTypes: () => bridge("tag_types"),
  setTagType: (file, nodeId, newType) => bridge("set_tag_type", file, nodeId, newType),
  setFigureAlt: (file, nodeId, alt) => bridge("set_figure_alt", file, nodeId, alt),
  makeDecorative: (file, nodeId) => bridge("make_decorative", file, nodeId),
  moveTag: (file, nodeId, delta) => bridge("move_tag", file, nodeId, delta),
  bulkEdit: (file, action, nodeIds, newType) =>
    bridge("bulk_edit", file, action, nodeIds, newType || null),
  carveTag: (file, picks, newType) => bridge("carve_tag", file, picks, newType),
  tagUntagged: (file, page, indexes, newType) =>
    bridge("tag_untagged", file, page, indexes, newType),
  unwrapTag: (file, nodeId) => bridge("unwrap_tag", file, nodeId),
  undoLastEdit: (file) => bridge("undo_last_edit", file),
  removeAllTags: (file) => bridge("remove_all_tags", file),
  exportHistory: () => bridge("export_history"),
  reprocess: (file) => bridge("reprocess", file),
  pageImage: (file, index) => bridge("page_image", file, index || 0),
  auditLog: (limit) => bridge("audit_log", limit),
  addPdfs: (module) => bridge("add_pdfs", module || null),
  decide: (file, reviewer, approve) => bridge("decide", file, reviewer, approve),
  openOutput: (file) => bridge("open_output", file),
  openErrorLog: () => bridge("open_error_log"),
  openInbox: (module) => bridge("open_inbox", module || null),
  openOutbox: () => bridge("open_outbox"),
};
