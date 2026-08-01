"""
Tracing Web Viewer Server - FastAPI application for trace visualization.
Tracing Web 可视化服务 —— 基于 FastAPI 的 trace 可视化 Web 应用。

Provides:
- HTML pages: Trace list + Trace detail (tree view)
- JSON API: /api/traces, /api/traces/{trace_id}

提供：
- HTML 页面：Trace 列表 + Trace 详情（树形视图）
- JSON API：/api/traces, /api/traces/{trace_id}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Manus Demo - Trace Viewer",
    description="Web-based visualization for OpenTelemetry traces",
    version="1.0.0",
)

# Templates directory (relative to this file)
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_traces_dir = Path("traces")
_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_NUMBERED_TRACE_FILE_RE = re.compile(
    r"^[0-9a-f]{32}-\d{4}-\d{2}-\d{2}-\d+\.json$"
)


def configure_traces_dir(path: str | Path) -> None:
    """Configure the local trace directory before serving requests."""
    global _traces_dir
    _traces_dir = Path(path).expanduser().resolve()


def _get_traces_dir() -> Path:
    return _traces_dir


# ---------------------------------------------------------------------------
# Span icon mapping (shared source of truth in spans.py)
# ---------------------------------------------------------------------------

from tracing.spans import SPAN_ICONS, DEFAULT_SPAN_ICON


def _get_span_icon(span_name: str) -> str:
    """Get icon for a span name based on prefix matching."""
    for prefix, icon in SPAN_ICONS.items():
        if span_name.startswith(prefix) or prefix in span_name:
            return icon
    return DEFAULT_SPAN_ICON


# ---------------------------------------------------------------------------
# Data access layer
# ---------------------------------------------------------------------------

def _load_all_traces() -> list[dict[str, Any]]:
    """
    Scan traces directory and load metadata for all trace files.
    扫描 traces 目录，加载所有 trace 文件的元数据。
    """
    traces_dir = _get_traces_dir()
    if not traces_dir.exists():
        return []

    candidates: list[tuple[float, Path]] = []
    for path in traces_dir.glob("*.json"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue

    results = []
    for _mtime, filepath in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            # Do not follow a trace-directory symlink to a file outside the
            # configured storage root. The detail endpoint applies the same
            # boundary check.
            if not filepath.resolve().is_relative_to(traces_dir.resolve()):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("spans", []), list):
                continue

            spans = [span for span in data.get("spans", []) if isinstance(span, dict)]
            trace_id = str(data.get("trace_id", filepath.stem))

            # Find root span (no parent_span_id)
            root_span = None
            for span in spans:
                if span.get("parent_span_id") is None:
                    root_span = span
                    break

            # Calculate total duration from root span or max of all root-level spans
            total_duration_ms = 0.0
            if root_span and root_span.get("duration_ms"):
                total_duration_ms = root_span["duration_ms"]
            elif spans:
                # Fallback: sum duration of all root-level spans (those without parent)
                total_duration_ms = sum(
                    s.get("duration_ms", 0) for s in spans if not s.get("parent_span_id")
                )

            # Determine overall status from root span (authoritative).
            # Tool retry-and-recovery cases produce child spans with ERROR but
            # the root task succeeded; reading any-ERROR would mislabel them ❌.
            # 顶层 status 取根 span，避免子 span 错误（如工具 retry 后恢复）误标整 trace。
            if root_span:
                status = root_span.get("status") or "UNSET"
                if status not in ("OK", "ERROR", "UNSET"):
                    status = "UNSET"
            elif not spans:
                status = "UNSET"
            elif any(s.get("status") == "ERROR" for s in spans):
                # Fallback when root span absent (rare): keep legacy any-ERROR rule
                status = "ERROR"
            else:
                status = "OK"

            results.append({
                "file_id": filepath.stem,
                "trace_id": trace_id,
                "root_span_name": root_span["name"] if root_span else "(no root)",
                "span_count": len(spans),
                "exported_at": data.get("exported_at", "N/A"),
                "total_duration_ms": round(total_duration_ms, 1),
                "status": status,
                "file_size_kb": round(filepath.stat().st_size / 1024, 1),
            })
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError, AttributeError):
            continue

    return results


def _resolve_trace_file(file_id: str) -> Path | None:
    """Resolve an exact file ID or a legacy trace-ID-only viewer link."""
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        return None

    traces_dir = _get_traces_dir()
    exact_path = traces_dir / f"{file_id}.json"
    if (
        exact_path.exists()
        and exact_path.resolve().is_relative_to(traces_dir.resolve())
    ):
        return exact_path

    # WebUI historically linked to /traces/{trace_id}. New numbered files use
    # a longer file_id, so keep those links working by resolving the newest
    # matching file. Hex validation prevents glob metacharacters in file_id.
    if _TRACE_ID_RE.fullmatch(file_id) is None:
        return None

    candidates: list[tuple[float, Path]] = []
    for filepath in traces_dir.glob(f"{file_id}-*.json"):
        try:
            if _NUMBERED_TRACE_FILE_RE.fullmatch(filepath.name) is None:
                continue
            if not filepath.resolve().is_relative_to(traces_dir.resolve()):
                continue
            candidates.append((filepath.stat().st_mtime, filepath))
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _load_trace(file_id: str) -> tuple[Path, dict[str, Any]] | None:
    """
    Load a single trace file by file_id (filename stem).
    根据 file_id（文件名主干）加载单个 trace 文件。
    """
    filepath = _resolve_trace_file(file_id)
    if filepath is None:
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("spans", []), list):
            return None
        data["spans"] = [span for span in data.get("spans", []) if isinstance(span, dict)]
        return filepath, data
    except (json.JSONDecodeError, OSError):
        return None


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """
    Build a tree structure from flat span list using parent_span_id.
    基于 parent_span_id 从扁平 span 列表构建树形结构。

    Handles edge cases:
    - Missing span_id: assigned a generated ID
    - Duplicate span_id: only the first occurrence is indexed
    - Self-referencing parent: treated as root
    """
    span_map: dict[str, dict] = {}
    roots: list[dict] = []
    generated_id_counter = 0

    # First pass: index all spans with robustness checks
    for span in spans:
        span_id = span.get("span_id") or ""
        if not span_id:
            generated_id_counter += 1
            span_id = f"__generated_{generated_id_counter}"

        # Skip duplicate span_ids (keep first occurrence)
        if span_id in span_map:
            continue

        span_copy = {**span, "span_id": span_id, "children": [], "icon": _get_span_icon(span.get("name", ""))}
        span_map[span_id] = span_copy

    # Second pass: build parent-child relationships
    for span_id, node in span_map.items():
        parent_id = node.get("parent_span_id")

        # Treat as root if: no parent, parent not in map, or self-referencing
        if not parent_id or parent_id not in span_map or parent_id == span_id:
            roots.append(node)
        else:
            span_map[parent_id]["children"].append(node)

    # Sort children by start_time at each level (with cycle detection)
    def sort_children(node: dict, visited: set | None = None) -> None:
        if visited is None:
            visited = set()
        node_id = node.get("span_id", id(node))
        if node_id in visited:
            return  # Break cycle
        visited.add(node_id)

        node["children"].sort(key=lambda c: c.get("start_time", ""))
        for child in node["children"]:
            sort_children(child, visited)

    roots.sort(key=lambda r: r.get("start_time", ""))
    for root in roots:
        sort_children(root)

    return roots


# ---------------------------------------------------------------------------
# Page routes (HTML)
# ---------------------------------------------------------------------------

@app.get("/", response_class=RedirectResponse)
async def index():
    """Redirect root to trace list page."""
    return RedirectResponse(url="/traces")


@app.get("/traces", response_class=HTMLResponse)
async def trace_list_page(request: Request):
    """Render the trace list page."""
    traces = _load_all_traces()
    return templates.TemplateResponse(request, "trace_list.html", {
        "traces": traces,
        "traces_dir": str(_get_traces_dir()),
    })


@app.get("/traces/{file_id}", response_class=HTMLResponse)
async def trace_detail_page(request: Request, file_id: str):
    """Render the trace detail page with tree view."""
    loaded = _load_trace(file_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Trace file not found: {file_id}")
    filepath, data = loaded

    spans = data.get("spans", [])
    tree = _build_span_tree(spans)
    trace_id = data.get("trace_id", file_id)

    return templates.TemplateResponse(request, "trace_detail.html", {
        "trace_id": trace_id,
        "file_id": filepath.stem,
        "exported_at": data.get("exported_at", "N/A"),
        "span_count": len(spans),
        "tree": tree,
    })


# ---------------------------------------------------------------------------
# API routes (JSON)
# ---------------------------------------------------------------------------

@app.get("/api/traces")
async def api_trace_list():
    """Return list of all traces as JSON."""
    return _load_all_traces()


@app.get("/api/traces/{file_id}")
async def api_trace_detail(file_id: str):
    """Return full trace data with tree structure as JSON."""
    loaded = _load_trace(file_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Trace file not found: {file_id}")
    filepath, data = loaded

    spans = data.get("spans", [])
    tree = _build_span_tree(spans)

    return {
        "file_id": filepath.stem,
        "trace_id": data.get("trace_id", file_id),
        "exported_at": data.get("exported_at", "N/A"),
        "span_count": len(spans),
        "tree": tree,
    }
