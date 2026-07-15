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
  loadPdf: (file) => bridge("load_pdf", file),
  runBatch: () => bridge("run_batch"),
  addPdfs: () => bridge("add_pdfs"),
  decide: (file, reviewer, approve) => bridge("decide", file, reviewer, approve),
  openOutput: (file) => bridge("open_output", file),
  openOutbox: () => bridge("open_outbox"),
};
