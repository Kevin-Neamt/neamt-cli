from __future__ import annotations

# dashboard/server.py — the official Neamt dashboard.
#
# Serves the React single-page app (dashboard/web, built from the neamt-ai
# dashboard client) and a slim API backed by Claude. Unlike the original
# neamt-ai dashboard — which proxied to a local "Mark" brain on :3000 — this
# runs entirely on the Anthropic API using the key stored in `neamt config`.
#
#   • Chat   : streamed Claude completions, conversations persisted in SQLite
#   • Studio : streamed HTML prototype generation, projects saved to disk
#   • Scribe : placeholder endpoints (the channel-archive pipeline is not part
#              of the core runtime; the single-video skill lives in `neamt`)
#
# Mark-only analytics pages (Memory/Neural/Optimizer/API Usage) are hidden in
# the client; their endpoints return empty defaults so nothing 500s.

import json
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from neamt import __version__
from neamt.bridge import handle_call
from neamt.config import get_config
from neamt.loader import Skill, discover_skills
from dashboard.chat_store import chat_store, title_from_message

# ── Paths & constants ──────────────────────────────────────────────────────────

_WEB_DIR      = Path(__file__).parent / "web"
_ASSETS_DIR   = _WEB_DIR / "assets"
_INDEX_HTML   = _WEB_DIR / "index.html"
_STUDIO_DIR   = Path.home() / ".neamt" / "data" / "studio" / "projects"
_SERVER_START = time.time()

CHAT_MODEL   = "claude-sonnet-4-6"
STUDIO_MODEL = "claude-sonnet-4-6"

app = FastAPI(title="Neamt Dashboard", version=__version__)

_skills: dict[str, Skill] = {}


def _reload_skills() -> None:
    _skills.clear()
    for skill in discover_skills():
        _skills[skill.manifest.id] = skill


_reload_skills()


# ── Anthropic helpers ──────────────────────────────────────────────────────────

def _anthropic_key() -> Optional[str]:
    try:
        return get_config("anthropic_api_key")
    except Exception:
        return None


def _client() -> Any:
    import anthropic
    key = _anthropic_key()
    if not key:
        raise RuntimeError("No Anthropic API key. Run: neamt config set anthropic_api_key <key>")
    return anthropic.Anthropic(api_key=key)


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _stream_claude(
    *, system: str, messages: list[dict[str, str]], model: str, max_tokens: int = 4096,
) -> Iterator[str]:
    """Yield SSE frames: {type:token,token} … then a final {type:done,…}.
    On failure yields a single {type:error,error}. Returns nothing; callers that
    need the full text should accumulate the tokens themselves."""
    started = time.time()
    try:
        client = _client()
    except Exception as e:  # missing key / import error
        yield _sse({"type": "error", "error": str(e)})
        return

    try:
        full = []
        with client.messages.stream(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        ) as stream:
            for text in stream.text_stream:
                full.append(text)
                yield _sse({"type": "token", "token": text})
        elapsed = int((time.time() - started) * 1000)
        yield _sse({"type": "done", "model": model, "time_ms": elapsed, "text": "".join(full)})
    except Exception as e:
        yield _sse({"type": "error", "error": str(e)})


# ── Skills (kept from the original core dashboard) ──────────────────────────────

class CallRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


@app.post("/api/skills/{skill_id}/call")
async def call_skill(skill_id: str, body: CallRequest) -> dict[str, Any]:
    skill = _skills.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return handle_call(skill, body.action, body.payload)


@app.get("/api/skills")
async def list_skills() -> list[dict[str, Any]]:
    return [
        {
            "id": s.manifest.id, "name": s.manifest.name,
            "version": s.manifest.version, "enabled": s.enabled,
        }
        for s in _skills.values()
    ]


# ── System ──────────────────────────────────────────────────────────────────────

@app.get("/api/system")
async def system() -> dict[str, Any]:
    has_key = bool(_anthropic_key())
    return {
        "version": f"v{__version__}",
        "uptime": int(time.time() - _SERVER_START),
        # The client's status pill reads `ollama` — here it reflects Claude.
        "ollama": {"connected": has_key, "models": [CHAT_MODEL] if has_key else []},
        "memory": {"facts": 0, "episodes": 0, "hippocampus": 0},
        "performance": "claude",
    }


# ── Chat (Claude) ───────────────────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are Mark, the assistant inside the Neamt dashboard — a local, modular AI "
    "platform. Be concise, friendly, and practical. Use Markdown when it helps."
)


class CreateConv(BaseModel):
    groupId: Optional[str] = None
    title: Optional[str] = None


class PatchConv(BaseModel):
    title: Optional[str] = None
    group_id: Optional[str] = None
    pinned: Optional[int] = None


class CreateGroup(BaseModel):
    name: str
    icon: Optional[str] = "folder"
    color: Optional[str] = "#FFFFFF"


class ChatMessage(BaseModel):
    message: str


@app.get("/api/chats")
async def chats_list() -> list[dict[str, Any]]:
    return chat_store().list_conversations()


@app.post("/api/chats")
async def chats_create(body: CreateConv) -> dict[str, Any]:
    return chat_store().create_conversation(title=body.title or "New chat", group_id=body.groupId)


@app.get("/api/chats/{cid}")
async def chats_get(cid: str) -> dict[str, Any]:
    store = chat_store()
    conv = store.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {**conv, "messages": store.list_messages(cid)}


@app.patch("/api/chats/{cid}")
async def chats_patch(cid: str, body: PatchConv) -> dict[str, Any]:
    store = chat_store()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    store.update_conversation(cid, patch)
    return store.get_conversation(cid) or {}


@app.delete("/api/chats/{cid}")
async def chats_delete(cid: str) -> dict[str, str]:
    chat_store().delete_conversation(cid)
    return {"status": "ok"}


@app.post("/api/chats/{cid}/message")
def chats_message(cid: str, body: ChatMessage) -> StreamingResponse:
    store = chat_store()
    conv = store.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # First user message titles the conversation.
    if conv["title"] == "New chat":
        store.update_conversation(cid, {"title": title_from_message(body.message)})

    history = store.history_for(cid)
    store.add_message(conversation_id=cid, role="user", content=body.message)
    messages = history + [{"role": "user", "content": body.message}]

    def gen() -> Iterator[str]:
        acc = []
        for frame in _stream_claude(system=_CHAT_SYSTEM, messages=messages, model=CHAT_MODEL):
            data = json.loads(frame[6:])
            if data.get("type") == "token":
                acc.append(data["token"])
            elif data.get("type") == "done":
                store.add_message(
                    conversation_id=cid, role="assistant",
                    content="".join(acc), model=CHAT_MODEL,
                )
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/chat-groups")
async def groups_list() -> list[dict[str, Any]]:
    return chat_store().list_groups()


@app.post("/api/chat-groups")
async def groups_create(body: CreateGroup) -> dict[str, Any]:
    return chat_store().create_group(body.name, body.icon or "folder", body.color or "#FFFFFF")


@app.delete("/api/chat-groups/{gid}")
async def groups_delete(gid: str) -> dict[str, str]:
    chat_store().delete_group(gid)
    return {"status": "ok"}


# ── Studio (Claude → self-contained HTML) ───────────────────────────────────────

_STUDIO_SYSTEM = (
    "You are a web prototype generator. The user describes an app or tool and you "
    "reply with a SINGLE complete, self-contained HTML file that implements it. "
    "All CSS inline in <style>, all JS inline in <script>, no external deps except "
    "cdnjs.cloudflare.com. It must work opened directly in a browser. Make it "
    "functional, not a mockup. Respond with ONLY the HTML, starting with <!DOCTYPE html>."
)

_FENCE = ("```html", "```")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith(_FENCE[0]):
        t = t[len(_FENCE[0]):]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith(_FENCE[1]):
        t = t[: -len(_FENCE[1])]
    return t.strip()


class StudioGen(BaseModel):
    prompt: str
    currentHtml: Optional[str] = None


class StudioSave(BaseModel):
    name: str
    html: str


def _studio_slug(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower())
    return safe.strip("-") or "untitled"


@app.get("/api/studio/projects")
async def studio_projects() -> dict[str, Any]:
    out = []
    if _STUDIO_DIR.exists():
        for p in _STUDIO_DIR.iterdir():
            f = p / "index.html"
            if f.exists():
                out.append({"name": p.name, "updated": int(f.stat().st_mtime)})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return {"projects": out}


@app.get("/api/studio/projects/{name}")
async def studio_get(name: str) -> dict[str, Any]:
    f = _STUDIO_DIR / _studio_slug(name) / "index.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return {"html": f.read_text(encoding="utf-8")}


@app.post("/api/studio/projects")
async def studio_save(body: StudioSave) -> dict[str, str]:
    d = _STUDIO_DIR / _studio_slug(body.name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(body.html, encoding="utf-8")
    return {"status": "ok", "name": _studio_slug(body.name)}


@app.delete("/api/studio/projects/{name}")
async def studio_delete(name: str) -> dict[str, str]:
    d = _STUDIO_DIR / _studio_slug(name)
    f = d / "index.html"
    if f.exists():
        f.unlink()
    try:
        d.rmdir()
    except OSError:
        pass
    return {"status": "ok"}


@app.post("/api/studio/generate")
def studio_generate(body: StudioGen) -> StreamingResponse:
    prompt = body.prompt
    if body.currentHtml:
        prompt = (
            f"Here is the current HTML:\n\n{body.currentHtml}\n\n"
            f"Apply this change and return the full updated file:\n{body.prompt}"
        )
    messages = [{"role": "user", "content": prompt}]

    def gen() -> Iterator[str]:
        acc = []
        for frame in _stream_claude(
            system=_STUDIO_SYSTEM, messages=messages, model=STUDIO_MODEL, max_tokens=8192,
        ):
            data = json.loads(frame[6:])
            if data.get("type") == "token":
                acc.append(data["token"])
                yield frame  # stream raw tokens to the code view
            elif data.get("type") == "done":
                html = _strip_fences("".join(acc))
                yield _sse({"type": "done", "html": html})
            else:
                yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Scribe (placeholder — channel pipeline is not part of the core runtime) ─────

@app.get("/api/scribe/logs")
async def scribe_logs() -> list[dict[str, Any]]:
    return []


@app.get("/api/scribe/videos")
async def scribe_videos() -> list[dict[str, Any]]:
    return []


@app.get("/api/scribe/stats")
async def scribe_stats() -> dict[str, Any]:
    return {"total": 0, "done": 0, "pending": 0, "failed": 0}


@app.get("/api/scribe/channel")
async def scribe_channel() -> dict[str, Any]:
    return {"channel": None, "configured": False}


@app.post("/api/scribe/scan")
async def scribe_scan() -> dict[str, str]:
    return {"status": "unavailable", "message": "Scribe channel pipeline is not enabled in this build."}


# ── Stubs for Mark-only pages (hidden in the client, kept crash-safe) ────────────

_EMPTY_OBJ: dict[str, Any] = {}


@app.get("/api/identity")
async def identity() -> dict[str, Any]:
    return {"name": "Mark", "persona": "Neamt assistant"}


@app.get("/api/personality")
async def personality() -> dict[str, Any]:
    return _EMPTY_OBJ


@app.get("/api/performance")
async def performance_get() -> dict[str, Any]:
    return {"profile": "claude"}


@app.post("/api/performance")
async def performance_set() -> dict[str, Any]:
    return {"profile": "claude"}


@app.get("/api/integrations")
async def integrations() -> dict[str, Any]:
    return {"telegram": False, "discord": False, "slack": False}


@app.get("/api/usage")
async def usage() -> list[Any]:
    return []


@app.get("/api/network")
async def network() -> dict[str, Any]:
    return {"peers": [], "enabled": False}


@app.get("/api/neurons")
async def neurons() -> list[Any]:
    return []


@app.get("/api/optimizer/status")
async def optimizer_status() -> dict[str, Any]:
    return {"running": False}


@app.get("/api/memory/{rest:path}")
async def memory_any(rest: str) -> list[Any]:
    return []


@app.get("/api/logs/{rest:path}")
async def logs_any(rest: str) -> list[Any]:
    return []


# ── Static SPA (must be registered last so /api wins) ───────────────────────────

if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> Any:
    # Serve real files (e.g. neamt-logo.svg) directly, else the SPA shell.
    candidate = _WEB_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(str(candidate))
    if _INDEX_HTML.exists():
        return FileResponse(str(_INDEX_HTML))
    return JSONResponse({"error": "dashboard not built"}, status_code=500)
