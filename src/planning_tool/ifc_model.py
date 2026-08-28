"""Per-project IFC storage and IFC → fragments conversion."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = PROJECT_ROOT / "viewer"
MODELS_DIR = PROJECT_ROOT / "data" / "models"
META_NAME = "model.json"


class IfcModelError(RuntimeError):
    pass


def project_model_dir(project_id: int) -> Path:
    return MODELS_DIR / str(int(project_id))


def ifc_path(project_id: int) -> Path:
    return project_model_dir(project_id) / "model.ifc"


def frag_path(project_id: int) -> Path:
    return project_model_dir(project_id) / "model.frag"


def meta_path(project_id: int) -> Path:
    return project_model_dir(project_id) / META_NAME


def mapping_path(project_id: int) -> Path:
    return project_model_dir(project_id) / "module_guids.json"


def status_path(project_id: int) -> Path:
    return project_model_dir(project_id) / "module_status.json"


def has_fragments(project_id: int) -> bool:
    path = frag_path(project_id)
    return path.is_file() and path.stat().st_size > 0


def save_ifc(project_id: int, source: Path) -> Path:
    source = Path(source)
    if not source.is_file():
        raise IfcModelError(f"IFC file not found: {source}")
    dest_dir = project_model_dir(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = ifc_path(project_id)
    shutil.copy2(source, dest)
    for stale in (frag_path(project_id), mapping_path(project_id), status_path(project_id)):
        if stale.exists():
            stale.unlink()
    meta_path(project_id).write_text(
        json.dumps({"source_name": source.name}, indent=2),
        encoding="utf-8",
    )
    return dest


def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        return shutil.which(f"{name}.cmd")
    return None


def _run(
    command: list[str],
    *,
    cwd: Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    lines: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        if on_line:
            on_line(line)
    code = proc.wait()
    if code != 0:
        detail = "\n".join(lines[-40:]) or f"exit {code}"
        raise IfcModelError(detail)


def ensure_viewer_ready(on_line: Optional[Callable[[str], None]] = None) -> None:
    """Install ThatOpen deps and build the static viewer if needed."""
    node = _which("node")
    npm = _which("npm")
    if not node or not npm:
        raise IfcModelError(
            "Node.js is required to convert and display IFC models. "
            "Install Node.js and make sure node/npm are on PATH."
        )
    modules = VIEWER_DIR / "node_modules" / "@thatopen" / "fragments"
    if not modules.exists():
        if on_line:
            on_line("Installing viewer packages (npm install)…")
        _run([npm, "install"], cwd=VIEWER_DIR, on_line=on_line)
    worker = VIEWER_DIR / "public" / "worker.mjs"
    if not worker.exists() and (VIEWER_DIR / "scripts" / "copy-worker.mjs").exists():
        _run([node, str(VIEWER_DIR / "scripts" / "copy-worker.mjs")], cwd=VIEWER_DIR)
    dist_index = VIEWER_DIR / "dist" / "index.html"
    if not dist_index.exists():
        if on_line:
            on_line("Building the 3D viewer…")
        _run([npm, "run", "build"], cwd=VIEWER_DIR, on_line=on_line)


def convert_project_ifc(
    project_id: int,
    on_line: Optional[Callable[[str], None]] = None,
) -> Path:
    ensure_viewer_ready(on_line=on_line)
    src = ifc_path(project_id)
    if not src.is_file():
        raise IfcModelError("No IFC model has been uploaded for this project.")
    dest = frag_path(project_id)
    node = _which("node")
    if not node:
        raise IfcModelError("Node.js was not found on PATH.")
    convert_js = VIEWER_DIR / "src" / "convert.mjs"
    if on_line:
        on_line("Converting IFC to fragments…")
    _run(
        [node, str(convert_js), str(src), str(dest)],
        cwd=VIEWER_DIR,
        on_line=on_line,
    )
    if not has_fragments(project_id):
        raise IfcModelError("Conversion finished but no fragments file was written.")
    save_guid_mapping(project_id, on_line=on_line)
    return dest


def save_guid_mapping(
    project_id: int,
    on_line: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    """Write module_guids.json. Never raises: missing Mark or ifcopenshell just skips."""
    src = ifc_path(project_id)
    if not src.is_file():
        return None
    try:
        from planning_tool.ifc_guid_map import extract_guid_marks, match_to_schedule
    except Exception as exc:
        if on_line:
            on_line(f"GUID mapping skipped: {exc}")
        return None
    try:
        if on_line:
            on_line("Reading GUID–Mark mapping from IFC…")
        rows = extract_guid_marks(src)
        grouped = match_to_schedule(rows, module_ids=None)["module_to_guids"]
        dest = mapping_path(project_id)
        dest.write_text(
            json.dumps(grouped, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if on_line:
            on_line(f"Mapped {len(grouped)} IFC marks to GUIDs.")
        return dest
    except Exception as exc:
        if on_line:
            on_line(f"GUID mapping skipped: {exc}")
        return None


def write_status_json(project_id: int, status_by_module: dict) -> Path:
    dest = status_path(project_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(status_by_module or {}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return dest


class _ViewerHandler(SimpleHTTPRequestHandler):
    frag_file: Path = Path()
    extra_files: dict[str, Path] = {}

    def log_message(self, format: str, *args) -> None:
        return

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/model.frag", "/model.frag/"):
            if not self.frag_file.is_file():
                self.send_error(404, "model.frag not found")
                return
            try:
                data = self.frag_file.read_bytes()
            except OSError as exc:
                self.send_error(500, str(exc))
                return
            self._send_bytes(data, "application/octet-stream")
            return
        extra = self.extra_files.get(path) or self.extra_files.get(path.rstrip("/"))
        if extra is not None:
            if not extra.is_file():
                self._send_bytes(b"{}", "application/json")
                return
            try:
                data = extra.read_bytes()
            except OSError:
                self._send_bytes(b"{}", "application/json")
                return
            self._send_bytes(data, "application/json")
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def start_viewer_server(
    frag_file: Path,
    extra_files: Optional[dict[str, Path]] = None,
) -> tuple[ThreadingHTTPServer, int]:
    dist = VIEWER_DIR / "dist"
    if not (dist / "index.html").is_file():
        raise IfcModelError("The 3D viewer has not been built yet.")

    directory = str(dist)

    class Handler(_ViewerHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    Handler.frag_file = Path(frag_file)
    Handler.extra_files = {
        key if key.startswith("/") else f"/{key}": Path(value)
        for key, value in (extra_files or {}).items()
    }
    Handler.extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".frag": "application/octet-stream",
    }

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return httpd, httpd.server_address[1]
