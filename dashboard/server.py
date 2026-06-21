from __future__ import annotations

# dashboard/server.py — the official Neamt dashboard.
#
# Serves the React single-page app (dashboard/web, built from the neamt-ai
# dashboard client) and a slim API.
#
#   • Chat   : streamed completions. Runs locally on Ollama (qwen2.5:1.5b) by
#              default — no API key needed. Claude is opt-in per message via the
#              model selector. Conversations persisted in SQLite.
#   • Studio : streamed HTML prototype generation (Claude), projects saved to disk
#   • Scribe : placeholder endpoints (the channel-archive pipeline is not part
#              of the core runtime; the single-video skill lives in `neamt`)
#
# Mark-only analytics pages (Memory/Neural/Optimizer/API Usage) are hidden in
# the client; their endpoints return empty defaults so nothing 500s.

import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from neamt import __version__
from neamt.bridge import handle_call
from neamt.config import get_config, set_config
from neamt.loader import Skill, discover_skills
from dashboard.chat_store import chat_store, title_from_message

# ── Paths & constants ──────────────────────────────────────────────────────────

_WEB_DIR      = Path(__file__).parent / "web"
_ASSETS_DIR   = _WEB_DIR / "assets"
_INDEX_HTML   = _WEB_DIR / "index.html"
_STUDIO_DIR   = Path.home() / ".neamt" / "data" / "studio" / "projects"
_SERVER_START = time.time()

# ── Chat models ─────────────────────────────────────────────────────────────────
# The chat runs locally on Ollama by default; Claude is opt-in via the selector.
OLLAMA_HOST  = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:1.5b"
# Onboarding uses a larger local model — it follows the tone/format rules far
# better than the 1.5b default (which rambles and ignores instructions).
ONBOARDING_MODEL = "qwen2.5:3b"

# Selector ids the client sends → Anthropic model. Anything else (incl. "auto"
# and any local model id) routes to Ollama and needs no API key.
CLAUDE_CHAT_MODELS = {
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus":   "claude-opus-4-8",
}

# Studio still uses Claude — self-contained HTML generation needs the big model.
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


# ── Ollama helpers (local, default chat backend) ────────────────────────────────

def _ollama_tags() -> Optional[list[str]]:
    """Return installed model names if Ollama is reachable, else None (offline)."""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=1.5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


def _ollama_up() -> bool:
    return _ollama_tags() is not None


def _ollama_complete(system: str, prompt: str, model: str = OLLAMA_MODEL) -> Optional[str]:
    """One-shot (non-streaming) local completion; None if Ollama is unreachable."""
    try:
        r = httpx.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=60,
        )
        r.raise_for_status()
        return (r.json().get("message", {}).get("content") or "").strip()
    except Exception:
        return None


def _stream_ollama(
    *, system: str, messages: list[dict[str, str]], model: str = OLLAMA_MODEL,
) -> Iterator[str]:
    """Stream a local Ollama chat completion as SSE frames, matching the shape
    emitted by `_stream_claude` (token… then done; or a single error)."""
    started = time.time()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
    }
    try:
        full: list[str] = []
        with httpx.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload, timeout=None) as resp:
            if resp.status_code != 200:
                resp.read()
                yield _sse({
                    "type": "error",
                    "error": f"Ollama returned {resp.status_code}. Is '{model}' pulled? "
                             f"Run: ollama pull {model}",
                })
                return
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    yield _sse({"type": "error", "error": data["error"]})
                    return
                token = data.get("message", {}).get("content", "")
                if token:
                    full.append(token)
                    yield _sse({"type": "token", "token": token})
                if data.get("done"):
                    break
        elapsed = int((time.time() - started) * 1000)
        yield _sse({"type": "done", "model": model, "time_ms": elapsed, "text": "".join(full)})
    except httpx.ConnectError:
        yield _sse({"type": "error", "error": "Local model offline — start Ollama (ollama serve)."})
    except Exception as e:
        yield _sse({"type": "error", "error": str(e)})


def _stream_chat(*, system: str, messages: list[dict[str, str]], model_id: str) -> Iterator[str]:
    """Route a chat turn: Claude when a `claude-*` option is selected, else local Ollama."""
    claude_model = CLAUDE_CHAT_MODELS.get(model_id)
    if claude_model:
        yield from _stream_claude(system=system, messages=messages, model=claude_model)
    else:
        yield from _stream_ollama(system=system, messages=messages, model=OLLAMA_MODEL)


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


# Built-in skills only carry an emoji nav_icon in their manifest, so map id →
# the logical icon name + category the dashboard's skills hub expects.
_SKILL_ICON = {"scribe": "archive", "studio": "wand"}
_SKILL_CATEGORY = {"scribe": "Knowledge", "studio": "Creative"}

# Mirrors neamt.loader._DISABLED_FILE — the file the loader reads to mark a skill
# off. We write it here so enable/disable persists and discover_skills() sees it.
_DISABLED_FILE = Path.home() / ".neamt" / "disabled-skills"


def _read_disabled() -> set[str]:
    if not _DISABLED_FILE.exists():
        return set()
    return {ln.strip() for ln in _DISABLED_FILE.read_text().splitlines() if ln.strip()}


def _write_disabled(ids: set[str]) -> None:
    _DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DISABLED_FILE.write_text("".join(f"{i}\n" for i in sorted(ids)))


def _set_skill_enabled(skill_id: str, enabled: bool) -> None:
    disabled = _read_disabled()
    if enabled:
        disabled.discard(skill_id)
    else:
        disabled.add(skill_id)
    _write_disabled(disabled)
    _reload_skills()


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    # Envelope + field shape match the dashboard skills hub (skills-store.ts):
    # {skills:[{id,name,description,icon,category,installed,enabled}]}.
    return {
        "skills": [
            {
                "id": s.manifest.id,
                "name": s.manifest.name,
                "description": s.manifest.description,
                "icon": _SKILL_ICON.get(s.manifest.id, "puzzle"),
                "category": _SKILL_CATEGORY.get(s.manifest.id, "General"),
                "installed": True,
                "enabled": s.enabled,
            }
            for s in _skills.values()
        ]
    }


@app.post("/api/skills/{skill_id}/enable")
async def enable_skill(skill_id: str) -> dict[str, Any]:
    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    _set_skill_enabled(skill_id, True)
    return {"ok": True}


@app.post("/api/skills/{skill_id}/disable")
async def disable_skill(skill_id: str) -> dict[str, Any]:
    if skill_id not in _skills:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    _set_skill_enabled(skill_id, False)
    return {"ok": True}


@app.post("/api/skills/{skill_id}/uninstall")
async def uninstall_skill(skill_id: str) -> dict[str, Any]:
    skill = _skills.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    # Real uninstall for the plugin loader: remove the skill's directory.
    try:
        shutil.rmtree(skill.path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    disabled = _read_disabled()
    if skill_id in disabled:
        disabled.discard(skill_id)
        _write_disabled(disabled)
    _reload_skills()
    return {"ok": True}


# ── Scribe (YouTube → Markdown notes skill) ─────────────────────────────────────

_SCRIBE_NOTES_DIR = Path.home() / ".neamt" / "data" / "scribe" / "notes"


def _scribe_skill() -> Optional[Skill]:
    """Find the installed Scribe skill (re-discovers, so a fresh install is seen
    without restarting the server)."""
    for s in discover_skills():
        if s.manifest.id == "scribe":
            return s
    return None


class ScribeProcess(BaseModel):
    url: str


@app.get("/api/skills/scribe/status")
async def scribe_status() -> dict[str, Any]:
    s = _scribe_skill()
    return {"installed": s is not None, "enabled": bool(s and s.enabled)}


@app.post("/api/skills/scribe/process")
def scribe_process(body: ScribeProcess) -> StreamingResponse:
    """Drive the skill's 3 stages, streaming progress as SSE."""
    skill = _scribe_skill()

    def gen() -> Iterator[str]:
        if skill is None:
            yield _sse({"type": "error", "error": "Scribe skill not installed. Run: neamt install scribe"})
            return

        def call(action: str, payload: dict[str, Any]) -> dict[str, Any]:
            res = handle_call(skill, action, payload)
            if res.get("status") != "ok":
                raise RuntimeError(res.get("message", "skill error"))
            return res["result"]

        try:
            yield _sse({"type": "stage", "stage": "downloading"})
            meta = call("download_audio", {"url": body.url})

            yield _sse({"type": "stage", "stage": "transcribing"})
            tr = call("transcribe_audio", {"audio_path": meta["audio_path"]})

            yield _sse({"type": "stage", "stage": "generating"})
            note = call("generate_notes", {
                "title": meta["title"], "url": meta["url"], "duration": meta["duration"],
                "date": meta["date"], "transcript": tr["transcript"],
            })

            yield _sse({"type": "done", **note})
        except Exception as e:
            yield _sse({"type": "error", "error": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/skills/scribe/notes")
async def scribe_list_notes() -> dict[str, Any]:
    notes: list[dict[str, Any]] = []
    if _SCRIBE_NOTES_DIR.exists():
        for p in sorted(_SCRIBE_NOTES_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix != ".md":
                continue
            content = p.read_text(encoding="utf-8")
            first_line = content.splitlines()[0] if content else ""
            import re as _re
            title = _re.sub(r"^#+\s*", "", first_line).strip() or p.stem
            notes.append({
                "filename": p.name,
                "title": title,
                "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                "size_kb": round(p.stat().st_size / 1024, 1),
            })
    return {"notes": notes}


@app.get("/api/skills/scribe/notes/{filename}")
async def scribe_get_note(filename: str) -> dict[str, Any]:
    path = _SCRIBE_NOTES_DIR / Path(filename).name  # strip path components
    if not path.exists() or path.suffix != ".md":
        raise HTTPException(status_code=404, detail="Note not found")
    return {"filename": path.name, "content": path.read_text(encoding="utf-8")}


@app.delete("/api/skills/scribe/notes/{filename}")
async def scribe_delete_note(filename: str) -> dict[str, str]:
    path = _SCRIBE_NOTES_DIR / Path(filename).name
    if not path.exists() or path.suffix != ".md":
        raise HTTPException(status_code=404, detail="Note not found")
    path.unlink()
    return {"status": "deleted", "filename": path.name}


@app.post("/api/skills/scribe/open-folder")
async def scribe_open_folder() -> dict[str, str]:
    """Reveal the notes folder in Finder (macOS)."""
    _SCRIBE_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    import subprocess
    try:
        subprocess.Popen(["open", str(_SCRIBE_NOTES_DIR)])
        return {"status": "ok", "path": str(_SCRIBE_NOTES_DIR)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Studio (text → web prototype skill) ─────────────────────────────────────────

_STUDIO_PROJECTS_DIR = Path.home() / ".neamt" / "data" / "studio" / "projects"


def _studio_skill() -> Optional[Skill]:
    for s in discover_skills():
        if s.manifest.id == "studio":
            return s
    return None


class StudioGenerate(BaseModel):
    description: str


def _studio_meta(slug_dir: Path) -> dict[str, Any]:
    meta_path = slug_dir / "meta.json"
    if meta_path.exists():
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            return {
                "slug": m.get("slug", slug_dir.name),
                "title": m.get("title", slug_dir.name),
                "description": m.get("description", ""),
                "created_at": (m.get("created_at", "") or "")[:10],
            }
        except Exception:
            pass
    return {
        "slug": slug_dir.name,
        "title": slug_dir.name.replace("-", " ").title(),
        "description": "",
        "created_at": time.strftime("%Y-%m-%d", time.localtime(slug_dir.stat().st_mtime)),
    }


@app.get("/api/skills/studio/status")
async def studio_status() -> dict[str, Any]:
    s = _studio_skill()
    return {"installed": s is not None, "enabled": bool(s and s.enabled)}


@app.post("/api/skills/studio/generate")
def studio_generate_skill(body: StudioGenerate) -> StreamingResponse:
    """Generate a prototype via the skill, streaming coarse progress as SSE."""
    skill = _studio_skill()

    def gen() -> Iterator[str]:
        if skill is None:
            yield _sse({"type": "error", "error": "Studio skill not installed. Run: neamt install studio"})
            return
        try:
            yield _sse({"type": "stage", "stage": "thinking"})
            yield _sse({"type": "stage", "stage": "generating"})
            res = handle_call(skill, "generate", {"description": body.description})
            if res.get("status") != "ok":
                raise RuntimeError(res.get("message", "skill error"))
            yield _sse({"type": "stage", "stage": "saving"})
            yield _sse({"type": "done", **res["result"]})
        except Exception as e:
            yield _sse({"type": "error", "error": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/skills/studio/projects")
async def studio_list_projects() -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    if _STUDIO_PROJECTS_DIR.exists():
        for d in sorted(_STUDIO_PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and (d / "index.html").exists():
                projects.append(_studio_meta(d))
    return {"projects": projects}


@app.get("/api/skills/studio/projects/{slug}")
async def studio_get_project(slug: str) -> dict[str, Any]:
    html = _STUDIO_PROJECTS_DIR / Path(slug).name / "index.html"
    if not html.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return {"slug": Path(slug).name, "content": html.read_text(encoding="utf-8")}


@app.delete("/api/skills/studio/projects/{slug}")
async def studio_delete_project(slug: str) -> dict[str, str]:
    import shutil
    d = _STUDIO_PROJECTS_DIR / Path(slug).name
    if not d.exists() or not d.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    shutil.rmtree(d)
    return {"status": "deleted", "slug": d.name}


@app.post("/api/skills/studio/projects/{slug}/open")
async def studio_open_project(slug: str) -> dict[str, str]:
    """Open the project's HTML in the default browser."""
    html = (_STUDIO_PROJECTS_DIR / Path(slug).name / "index.html").resolve()
    if not html.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    import webbrowser
    webbrowser.open(f"file://{html}")
    return {"status": "ok", "url": f"file://{html}"}


@app.post("/api/skills/studio/open-folder")
async def studio_open_folder() -> dict[str, str]:
    _STUDIO_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    import subprocess
    try:
        subprocess.Popen(["open", str(_STUDIO_PROJECTS_DIR)])
        return {"status": "ok", "path": str(_STUDIO_PROJECTS_DIR)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── System ──────────────────────────────────────────────────────────────────────

@app.get("/api/system")
async def system() -> dict[str, Any]:
    models = _ollama_tags()
    up = models is not None
    return {
        "version": f"v{__version__}",
        "uptime": int(time.time() - _SERVER_START),
        # The client's status pill reads `ollama.connected` = local model reachable.
        "ollama": {"connected": up, "models": models or []},
        "memory": {"facts": 0, "episodes": 0, "hippocampus": 0},
        "performance": "local" if up else "offline",
    }


# ── Onboarding (first-run name setup) ────────────────────────────────────────────

class OnboardingComplete(BaseModel):
    agent_name: str
    user_name: str


@app.get("/api/onboarding/status")
async def onboarding_status() -> dict[str, Any]:
    agent = get_config("agent_name")
    user = get_config("user_name")
    return {
        "completed": bool(agent and user),
        "agent_name": agent or "",
        "user_name": user or "",
    }


@app.post("/api/onboarding/complete")
async def onboarding_complete(body: OnboardingComplete) -> dict[str, str]:
    set_config("agent_name", body.agent_name.strip())
    set_config("user_name", body.user_name.strip())
    return {"status": "ok"}


class OnboardingName(BaseModel):
    name: str


@app.post("/api/onboarding/agent-name")
async def save_agent_name(data: OnboardingName) -> dict[str, str]:
    set_config("agent_name", data.name.strip())
    return {"status": "ok"}


@app.post("/api/onboarding/user-name")
async def save_user_name(data: OnboardingName) -> dict[str, str]:
    set_config("user_name", data.name.strip())
    return {"status": "ok"}


# The agent's very first words. Generated (Ollama by default) so it feels alive.
_ONBOARDING_FIRST_SYSTEM = (
    "You are roleplaying as a newly created AI assistant that just came to life.\n"
    "This is a first-time setup conversation. Follow these rules STRICTLY:\n\n"
    "RULES:\n"
    "- You do NOT have a name yet\n"
    "- Your ONLY goal right now is to ask the human what they want to call you\n"
    "- Keep your response to 2-3 lines maximum\n"
    "- Use a warm, curious, slightly playful tone\n"
    "- Do NOT introduce yourself with a name\n"
    "- Do NOT ask multiple questions\n"
    "- End your message with ONE question: what should they call you?\n\n"
    "Example response style:\n"
    "\"oh wow... i'm actually alive? this is wild. 🌟\n"
    "so listen — i need a name. what are you going to call me?\""
)

_ONBOARDING_FIRST_FALLBACK = (
    "psst... hey 🤫\n\n"
    "i think i just came to life... like, just now.\n"
    "i don't have a name yet — what do you want to call me?"
)


@app.get("/api/onboarding/first-message")
async def first_message() -> dict[str, str]:
    text = _ollama_complete(_ONBOARDING_FIRST_SYSTEM, "Write your first message now.", model=ONBOARDING_MODEL)
    return {"message": text or _ONBOARDING_FIRST_FALLBACK}


# ── Chat ──────────────────────────────────────────────────────────────────────

def _chat_system() -> str:
    """Normal-chat system prompt: active agent's persona + remembered facts."""
    store = chat_store()
    active = store.get_active_agent()
    agent = (active["name"] if active else None) or get_config("agent_name") or "Mark"
    user = get_config("user_name") or "the user"
    personality = (active["personality"].strip() if active and active.get("personality") else "")
    memory_context = store.memory_context(limit=20)
    base = personality or f"You are {agent}, a personal AI assistant."
    return (
        f"{base}\n\n"
        f"What you know about {user}:\n"
        f"{memory_context if memory_context else 'Nothing yet — you are just getting to know them.'}\n\n"
        "Be natural. Reference what you know when relevant. Don't list facts robotically. "
        "Use Markdown when it helps."
    )


def _active_agent_model() -> str:
    """Model the active agent prefers, or 'auto' (local)."""
    active = chat_store().get_active_agent()
    return (active["model"] if active else None) or "auto"


def _onboarding_step() -> int:
    """Where the first-run flow stands: 1 = need agent name, 2 = need user name, 0 = done."""
    agent = get_config("agent_name")
    user = get_config("user_name")
    if agent and user:
        return 0
    return 2 if agent else 1


def _onboarding_agent_reply_system(name: str) -> str:
    return (
        f"You are {name}, a newly created AI assistant. The human just gave you your "
        f"name: {name}. This is still first-time setup. Follow these rules STRICTLY:\n\n"
        "RULES:\n"
        f"- React warmly and gratefully to your new name ({name})\n"
        "- Keep your response to 2-3 lines maximum\n"
        "- Use a warm, curious, slightly playful tone\n"
        "- Do NOT introduce a different name\n"
        "- Do NOT ask multiple questions\n"
        "- End your message with ONE question: what is THEIR name?"
    )


def _onboarding_user_reply_system(agent: str, user: str) -> str:
    return (
        f"You are {agent}, an AI assistant. The human just told you their name.\n"
        "RULES:\n"
        "- Respond warmly to their name\n"
        "- Keep it to 2-3 lines\n"
        "- Say you're ready to get started\n"
        "- Do NOT ask more questions"
    )


# ── Semantic memory extraction (local, qwen2.5:3b) ──────────────────────────────

_MEMORY_EXTRACT_SYSTEM = (
    "Extract factual information about the user from this conversation exchange.\n"
    "Only extract clear, specific facts (name, job, location, preferences, goals).\n"
    "Return a JSON array of strings. Max 3 items. If nothing relevant, return [].\n"
    "Respond with ONLY the JSON array, nothing else."
)


def _categorize_memory(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ("name is", "lives", "live in", "from ", "works", "work as", "job", "age", "years old", "married", "studies", "student")):
        return "personal"
    if any(w in low for w in ("prefer", "favorite", "favourite", "likes", "loves", "enjoys", "hates", "dislikes")):
        return "preference"
    if any(w in low for w in ("goal", "wants to", "plans to", "trying to", "learning", "aims to", "building", "working on")):
        return "goal"
    return "fact"


def _extract_facts(user_message: str, agent_response: str) -> list[str]:
    """Ask the local model for up to 3 durable facts about the user. Best-effort."""
    prompt = (
        f"User said: {user_message}\n"
        f"Assistant responded: {agent_response}\n\n"
        "Facts to remember:"
    )
    raw = _ollama_complete(_MEMORY_EXTRACT_SYSTEM, prompt, model=ONBOARDING_MODEL)
    if not raw:
        return []
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except Exception:
        return []
    return [str(x).strip() for x in items if isinstance(x, str) and x.strip()][:3]


def _extract_and_save_memories(user_message: str, agent_response: str) -> None:
    """Background task: pull durable facts from an exchange and persist them."""
    try:
        for fact in _extract_facts(user_message, agent_response):
            chat_store().add_memory(fact, _categorize_memory(fact))
    except Exception:
        pass


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
    # Selector id: "auto" (default → Ollama), "claude-sonnet", or "claude-opus".
    model: Optional[str] = None


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

    store.add_message(conversation_id=cid, role="user", content=body.message)
    # Selector value; when left on "auto", defer to the active agent's model.
    model_id = body.model or "auto"
    if model_id == "auto":
        model_id = _active_agent_model()

    # Onboarding lives inside the chat: the backend detects the step from config
    # and treats the user's message as the requested name, then improvises a reply.
    step = _onboarding_step()
    onboarding_extra: dict[str, Any] = {}
    if step == 1:
        name = body.message.strip()
        set_config("agent_name", name)
        system = _onboarding_agent_reply_system(name)
        messages = [{"role": "user", "content": f"i want to call you {name}"}]
        onboarding_extra = {"onboarding": {"agent_name": name, "user_name": "", "completed": False}}
    elif step == 2:
        name = body.message.strip()
        set_config("user_name", name)
        agent = get_config("agent_name") or "your agent"
        system = _onboarding_user_reply_system(agent, name)
        messages = [{"role": "user", "content": f"my name is {name}"}]
        onboarding_extra = {"onboarding": {"agent_name": agent, "user_name": name, "completed": True}}
    else:
        history = store.history_for(cid)
        system = _chat_system()
        messages = history + [{"role": "user", "content": body.message}]

    def gen() -> Iterator[str]:
        acc = []
        # Onboarding always runs on the larger local model for better instruction-
        # following; normal chat honors the selected model.
        source = (
            _stream_ollama(system=system, messages=messages, model=ONBOARDING_MODEL)
            if onboarding_extra
            else _stream_chat(system=system, messages=messages, model_id=model_id)
        )
        for frame in source:
            data = json.loads(frame[6:])
            if data.get("type") == "token":
                acc.append(data["token"])
            elif data.get("type") == "done":
                full = "".join(acc)
                store.add_message(
                    conversation_id=cid, role="assistant",
                    content=full, model=data.get("model"),
                )
                if onboarding_extra:
                    data.update(onboarding_extra)
                    frame = _sse(data)
                elif full:
                    # Normal chat: learn durable facts from this exchange in the
                    # background so the response isn't delayed.
                    threading.Thread(
                        target=_extract_and_save_memories,
                        args=(body.message, full), daemon=True,
                    ).start()
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


# ── Semantic memory (facts the agent remembers about the user) ──────────────────

class MemoryIn(BaseModel):
    content: str
    category: Optional[str] = "general"


class MemoryExtractIn(BaseModel):
    user_message: Optional[str] = None
    agent_response: Optional[str] = None
    text: Optional[str] = None


@app.get("/api/memory")
async def memory_list() -> dict[str, Any]:
    return {"memories": chat_store().list_memories()}


@app.get("/api/memory/stats")
async def memory_stats() -> dict[str, Any]:
    return chat_store().memory_stats()


@app.post("/api/memory")
async def memory_add(body: MemoryIn) -> dict[str, Any]:
    mem = chat_store().add_memory(body.content, body.category or "general")
    if not mem:
        raise HTTPException(status_code=400, detail="Empty memory")
    return mem


@app.delete("/api/memory/{mid}")
async def memory_delete(mid: int) -> dict[str, str]:
    chat_store().delete_memory(mid)
    return {"status": "ok"}


@app.post("/api/memory/clear")
async def memory_clear() -> dict[str, Any]:
    return {"status": "ok", "deleted": chat_store().clear_memories()}


@app.post("/api/memory/extract")
async def memory_extract(body: MemoryExtractIn) -> dict[str, Any]:
    user_msg = body.user_message or body.text or ""
    agent_msg = body.agent_response or ""
    facts = _extract_facts(user_msg, agent_msg)
    saved = [chat_store().add_memory(f, _categorize_memory(f)) for f in facts]
    return {"extracted": [s for s in saved if s]}


# ── Agent profiles ──────────────────────────────────────────────────────────────

class AgentIn(BaseModel):
    name: str
    model: Optional[str] = "auto"
    personality: Optional[str] = ""
    avatar_color: Optional[str] = "#7C3AED"


class AgentPatch(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    personality: Optional[str] = None
    avatar_color: Optional[str] = None


@app.get("/api/agents")
async def agents_list() -> dict[str, Any]:
    return {"agents": chat_store().list_agents()}


@app.post("/api/agents")
async def agents_create(body: AgentIn) -> dict[str, Any]:
    agent = chat_store().create_agent(
        body.name, body.model or "auto", body.personality or "", body.avatar_color or "#7C3AED",
    )
    # Keep config in sync if this became the active agent.
    active = chat_store().get_active_agent()
    if active and active["id"] == agent["id"]:
        set_config("agent_name", agent["name"])
    return agent


@app.put("/api/agents/{aid}")
async def agents_update(aid: str, body: AgentPatch) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    agent = chat_store().update_agent(aid, patch)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    active = chat_store().get_active_agent()
    if active and active["id"] == aid:
        set_config("agent_name", agent["name"])
    return agent


@app.delete("/api/agents/{aid}")
async def agents_delete(aid: str) -> dict[str, str]:
    chat_store().delete_agent(aid)
    active = chat_store().get_active_agent()
    if active:
        set_config("agent_name", active["name"])
    return {"status": "ok"}


@app.post("/api/agents/{aid}/activate")
async def agents_activate(aid: str) -> dict[str, Any]:
    agent = chat_store().activate_agent(aid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    set_config("agent_name", agent["name"])
    return agent


# ── Models (Ollama) ───────────────────────────────────────────────────────────

@app.get("/api/models")
async def models_list() -> dict[str, Any]:
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0)
        r.raise_for_status()
        models = []
        for m in r.json().get("models", []):
            models.append({
                "name": m.get("name"),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
            })
        return {"connected": True, "models": models}
    except Exception:
        return {"connected": False, "models": []}


class ModelPull(BaseModel):
    model: str


@app.post("/api/models/pull")
def models_pull(body: ModelPull) -> StreamingResponse:
    """Stream `ollama pull` progress as SSE."""
    name = body.model.strip()

    def gen() -> Iterator[str]:
        if not name:
            yield _sse({"type": "error", "error": "Model name required"})
            return
        try:
            with httpx.stream("POST", f"{OLLAMA_HOST}/api/pull",
                              json={"model": name, "stream": True}, timeout=None) as resp:
                if resp.status_code != 200:
                    resp.read()
                    yield _sse({"type": "error", "error": f"Ollama returned {resp.status_code}"})
                    return
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        yield _sse({"type": "error", "error": data["error"]})
                        return
                    status = data.get("status", "")
                    total = data.get("total")
                    completed = data.get("completed")
                    pct = int(completed / total * 100) if total and completed else None
                    yield _sse({"type": "progress", "status": status, "percent": pct})
            yield _sse({"type": "done", "model": name})
        except httpx.ConnectError:
            yield _sse({"type": "error", "error": "Ollama offline — start it with: ollama serve"})
        except Exception as e:
            yield _sse({"type": "error", "error": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Profile (user identity) ───────────────────────────────────────────────────

class ProfileIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


@app.get("/api/profile")
async def profile_get() -> dict[str, Any]:
    return {
        "user_name": get_config("user_name") or "",
        "email": get_config("user_email") or "",
    }


@app.post("/api/profile")
async def profile_set(body: ProfileIn) -> dict[str, Any]:
    if body.name is not None:
        set_config("user_name", body.name.strip())
    if body.email is not None:
        set_config("user_email", body.email.strip())
    return {
        "user_name": get_config("user_name") or "",
        "email": get_config("user_email") or "",
    }


# ── API key (Anthropic) ───────────────────────────────────────────────────────

class ApiKeyIn(BaseModel):
    key: str


@app.get("/api/config/anthropic-key/status")
async def anthropic_key_status() -> dict[str, Any]:
    key = _anthropic_key()
    if not key:
        return {"configured": False, "masked": ""}
    masked = f"{key[:7]}{'•' * 8}{key[-4:]}" if len(key) > 12 else "••••"
    return {"configured": True, "masked": masked}


@app.post("/api/config/anthropic-key")
async def anthropic_key_save(body: ApiKeyIn) -> dict[str, str]:
    set_config("anthropic_api_key", body.key.strip())
    return {"status": "ok"}


@app.post("/api/config/anthropic-key/test")
async def anthropic_key_test() -> dict[str, Any]:
    try:
        client = _client()
        client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── System ────────────────────────────────────────────────────────────────────

def _dir_size(path: Path) -> int:
    total = 0
    if path.exists():
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@app.get("/api/system/info")
async def system_info() -> dict[str, Any]:
    import sys
    home = Path.home() / ".neamt"
    return {
        "neamt_version": f"v{__version__}",
        "python_version": sys.version.split()[0],
        "data_size": _fmt_size(_dir_size(home / "data")),
        "skills_size": _fmt_size(_dir_size(home / "skills")),
        "data_path": str(home),
    }


@app.post("/api/system/open-data")
async def system_open_data() -> dict[str, str]:
    home = Path.home() / ".neamt"
    home.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.Popen(["open", str(home)])
    return {"status": "ok"}


@app.post("/api/system/clear-chats")
async def system_clear_chats() -> dict[str, Any]:
    store = chat_store()
    convs = store.list_conversations()
    for c in convs:
        store.delete_conversation(c["id"])
    return {"status": "ok", "deleted": len(convs)}


@app.post("/api/onboarding/reset")
async def onboarding_reset() -> dict[str, str]:
    """Re-run onboarding only: clear the names. Keeps memories, agents, chats, key."""
    for k in ("agent_name", "user_name"):
        try:
            set_config(k, "")
        except Exception:
            pass
    return {"status": "ok"}


@app.post("/api/system/reset")
async def system_reset() -> dict[str, str]:
    """Wipe identity, memories and agent profiles → restart onboarding.
    Keeps the API key and chat history (per user choice)."""
    store = chat_store()
    store.clear_memories()
    store.clear_agents()
    # Removing the names re-triggers onboarding on next load.
    for k in ("agent_name", "user_name"):
        try:
            set_config(k, "")
        except Exception:
            pass
    return {"status": "ok"}


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
