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
  loadPdf: (file) => bridge("load_pdf", file),
  runBatch: () => bridge("run_batch"),
  runBatchStart: () => bridge("run_batch_start"),
  runBatchStatus: () => bridge("run_batch_status"),
  runBatchCancel: () => bridge("run_batch_cancel"),
  docMetadata: (file) => bridge("doc_metadata", file),
  setDocMetadata: (file, title, lang) => bridge("set_doc_metadata", file, title, lang),
  structure: (file) => bridge("structure", file),
  structureTree: (file) => bridge("structure_tree", file),
  pageImage: (file, index) => bridge("page_image", file, index || 0),
  auditLog: (limit) => bridge("audit_log", limit),
  addPdfs: () => bridge("add_pdfs"),
  decide: (file, reviewer, approve) => bridge("decide", file, reviewer, approve),
  openOutput: (file) => bridge("open_output", file),
  openInbox: () => bridge("open_inbox"),
  openOutbox: () => bridge("open_outbox"),
};
