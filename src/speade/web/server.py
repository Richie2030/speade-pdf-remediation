"""The FastAPI app behind the localhost web client.

A thin HTTP skin over the same SpeadeApi bridge the desktop window uses --
no pipeline or gate logic here (that is service.py's job, once, for every
front door). Endpoints mirror the bridge method-for-method; the two places
a browser cannot do what a native window can get substitutes:

- add_pdfs (native file dialog)  -> POST /api/upload (multipart into the inbox)
- embedded preview via data: URI -> GET /api/pdf (the browser renders it inline)

open_output / open_outbox still work: the server IS the user's machine
(127.0.0.1 only), so os.startfile lands on their own desktop.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from speade import service
from speade.desktop.api import SpeadeApi
from speade.service import DEFAULT_CONFIG_PATH

UI_DIR = Path(__file__).resolve().parent.parent / "desktop" / "ui"
WEB_API_JS = Path(__file__).resolve().parent / "api.js"


class DecideBody(BaseModel):
    file: str
    reviewer: str
    approve: bool


class BatchStartBody(BaseModel):
    module: str | None = None
    reprocess: bool = False


class OpenInboxBody(BaseModel):
    module: str | None = None


class AutoCheckBody(BaseModel):
    on: bool


class MetadataBody(BaseModel):
    file: str
    title: str = ""
    lang: str = ""


class FileBody(BaseModel):
    file: str


class TagTypeBody(BaseModel):
    file: str
    node_id: int
    new_type: str


class AltBody(BaseModel):
    file: str
    node_id: int
    alt: str = ""


class NodeBody(BaseModel):
    file: str
    node_id: int


class MoveBody(BaseModel):
    file: str
    node_id: int
    delta: int


class BulkBody(BaseModel):
    file: str
    action: str
    node_ids: list[int]
    new_type: str | None = None


class CarvePick(BaseModel):
    node_id: int
    mcid: int


class CarveBody(BaseModel):
    file: str
    picks: list[CarvePick]
    new_type: str


class TagNewBody(BaseModel):
    file: str
    page: int
    indexes: list[int]
    new_type: str


def create_app(config_path: Path | None = None) -> FastAPI:
    cfg = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    bridge = SpeadeApi(cfg)  # headless: no window; uploads replace the native dialog
    app = FastAPI(title="SPEADE PDF Accessibility", docs_url=None, redoc_url=None)
    # __main__ calls bridge.shutdown() after uvicorn returns: a Ctrl+C mid-batch
    # must not let exit finalizers race the worker's native pdfium call.
    app.state.bridge = bridge

    # ------------------------------------------------------------- the UI
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.get("/api.js")
    def api_js() -> FileResponse:
        # THE seam: the web delivery's api.js shadows the desktop one.
        return FileResponse(WEB_API_JS, media_type="text/javascript")

    # ------------------------------------------------------------ read side
    @app.get("/api/workspace")
    def workspace() -> dict:
        return bridge.workspace()

    @app.get("/api/reviewer_default")
    def reviewer_default() -> dict:
        return {"reviewer": bridge.reviewer_default()}

    @app.get("/api/list_queue")
    def list_queue() -> list[dict]:
        return bridge.list_queue()

    @app.get("/api/list_pending")
    def list_pending() -> list[str]:
        return bridge.list_pending()

    @app.get("/api/modules")
    def modules() -> dict:
        return {"modules": bridge.modules()}

    @app.get("/api/auto_check")
    def auto_check() -> dict:
        return {"auto_check": bridge.auto_check()}

    @app.post("/api/auto_check")
    def set_auto_check(body: AutoCheckBody) -> dict:
        return bridge.set_auto_check(body.on)

    @app.post("/api/check_now")
    def check_now(body: FileBody) -> dict:
        return bridge.check_now(body.file)

    @app.get("/api/pdf")
    def pdf(file: str):
        try:
            found = service.find_output(file, cfg)
        except ValueError:  # malformed rel-id: a clean 404, never a 500
            found = None
        if found is None:
            return JSONResponse({"error": f"not found: {Path(file).name}"}, status_code=404)
        return FileResponse(found, media_type="application/pdf")

    @app.get("/api/structure")
    def structure(file: str) -> dict:
        return bridge.structure(file)

    @app.get("/api/structure_tree")
    def structure_tree(file: str) -> dict:
        return bridge.structure_tree(file)

    @app.get("/api/page_image")
    def page_image(file: str, index: int = 0) -> dict:
        return bridge.page_image(file, index)

    @app.get("/api/metadata")
    def metadata(file: str) -> dict:
        return bridge.doc_metadata(file)

    @app.get("/api/audit_log")
    def audit_log(limit: int = 200) -> list[dict]:
        return bridge.audit_log(limit)

    @app.get("/api/run_batch_status")
    def run_batch_status() -> dict:
        return bridge.run_batch_status()

    # ----------------------------------------------------------- write side
    @app.post("/api/run_batch_start")
    def run_batch_start(body: BatchStartBody | None = None) -> dict:
        body = body or BatchStartBody()
        return bridge.run_batch_start(reprocess=body.reprocess, module=body.module)

    @app.post("/api/run_batch_cancel")
    def run_batch_cancel() -> dict:
        return bridge.run_batch_cancel()

    @app.post("/api/decide")
    def decide(body: DecideBody):
        try:
            return bridge.decide(body.file, body.reviewer, body.approve)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/metadata")
    def set_metadata(body: MetadataBody) -> dict:
        return bridge.set_doc_metadata(body.file, body.title, body.lang)

    @app.get("/api/tag_types")
    def tag_types() -> list[str]:
        return bridge.tag_types()

    @app.post("/api/tag_type")
    def set_tag_type(body: TagTypeBody) -> dict:
        return bridge.set_tag_type(body.file, body.node_id, body.new_type)

    @app.post("/api/figure_alt")
    def set_figure_alt(body: AltBody) -> dict:
        return bridge.set_figure_alt(body.file, body.node_id, body.alt)

    @app.post("/api/decorative")
    def make_decorative(body: NodeBody) -> dict:
        return bridge.make_decorative(body.file, body.node_id)

    @app.post("/api/move_tag")
    def move_tag(body: MoveBody) -> dict:
        return bridge.move_tag(body.file, body.node_id, body.delta)

    @app.post("/api/bulk_edit")
    def bulk_edit(body: BulkBody) -> dict:
        return bridge.bulk_edit(body.file, body.action, body.node_ids, body.new_type)

    @app.post("/api/carve_tag")
    def carve_tag(body: CarveBody) -> dict:
        return bridge.carve_tag(body.file, [p.model_dump() for p in body.picks], body.new_type)

    @app.post("/api/tag_untagged")
    def tag_untagged(body: TagNewBody) -> dict:
        return bridge.tag_untagged(body.file, body.page, body.indexes, body.new_type)

    @app.post("/api/unwrap_tag")
    def unwrap_tag(body: NodeBody) -> dict:
        return bridge.unwrap_tag(body.file, body.node_id)

    @app.post("/api/undo_last_edit")
    def undo_last_edit(body: FileBody) -> dict:
        return bridge.undo_last_edit(body.file)

    @app.post("/api/remove_all_tags")
    def remove_all_tags(body: FileBody) -> dict:
        return bridge.remove_all_tags(body.file)

    @app.post("/api/export_history")
    def export_history() -> dict:
        return bridge.export_history()

    @app.post("/api/reprocess")
    def reprocess(body: FileBody) -> dict:
        return bridge.reprocess(body.file)

    @app.post("/api/upload")
    async def upload(files: list[UploadFile], module: str = Form("")) -> dict:
        """The browser's stand-in for the native Add PDFs dialog: multipart
        uploads land in the inbox (the given module's folder), names stripped
        of any path components."""
        code: str | None = None
        if module.strip():
            try:
                code = service.normalize_module(module)
            except ValueError as exc:
                return {"error": str(exc)}
        inbox = service.workspace(cfg).inbox
        if code:
            inbox = inbox / code
            inbox.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for item in files:
            name = Path(item.filename or "").name
            if not name.lower().endswith(".pdf"):
                continue
            (inbox / name).write_bytes(await item.read())
            copied.append(service.make_id(code, name))
        return {"copied": copied}

    @app.post("/api/open_output")
    def open_output(body: FileBody) -> dict:
        return {"ok": bridge.open_output(body.file)}

    @app.post("/api/open_error_log")
    def open_error_log() -> dict:
        return bridge.open_error_log()

    @app.post("/api/open_inbox")
    def open_inbox(body: OpenInboxBody | None = None) -> dict:
        return {"ok": bridge.open_inbox((body or OpenInboxBody()).module)}

    @app.post("/api/open_outbox")
    def open_outbox() -> dict:
        return {"ok": bridge.open_outbox()}

    # static assets last: routes above (incl. /api.js) win over the mount.
    app.mount("/", StaticFiles(directory=UI_DIR), name="ui")
    return app
