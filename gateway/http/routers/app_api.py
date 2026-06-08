"""Top-level /api/* routes (no /v1 version prefix).

These are called by the UI before authentication (getBackendConfig) and after
(getModels, getChangelog…).  They follow the OpenWebUI /api/* contract so the
SvelteKit frontend can bootstrap without modification.
"""

import asyncio
import json
import time as _time
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from config import settings
from configs.service import ConfigsService
from data.database import get_db
from data.models import User
from gateway.http.dependencies import get_current_user
from models.service import ModelsService
from media.images import ImagesService
from providers.config_service import ProviderConfigService
from providers.proxy import proxy_json, proxy_stream

router = APIRouter(tags=["app-api"])


def _models_svc(db: Session = Depends(get_db)) -> ModelsService:
    return ModelsService(db)


def _images_svc(db: Session = Depends(get_db)) -> ImagesService:
    return ImagesService(db)


# ── /api/config ───────────────────────────────────────────────────────────────


@router.get("/config")
def get_backend_config(db: Session = Depends(get_db)):
    """Bootstrap config read by the UI before the user logs in."""
    images_cfg = ImagesService(db).get_config()
    models_cfg = ConfigsService(db).get("models") or {}
    return {
        "status": True,
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "default_locale": "en-US",
        "default_models": models_cfg.get("DEFAULT_MODELS", ""),
        "default_prompt_suggestions": [],
        "features": {
            "auth": True,
            "auth_trusted_header": False,
            "enable_api_key": True,
            "enable_signup": True,
            "enable_login_form": True,
            "enable_web_search": False,
            "enable_google_drive_integration": False,
            "enable_image_generation": bool(images_cfg.get("enabled")),
            "enable_admin_export": True,
            "enable_admin_chat_access": True,
            "enable_community_sharing": False,
            "enable_autocomplete_generation": False,
            "enable_websocket": True,
        },
        "oauth": {"providers": {}},
        "audio": {
            "stt": {
                "engine": "",
            },
            "tts": {
                "engine": "",
                "voice": "",
                "split_on": "punctuation",
                "auto_playback": False,
            },
        },
    }


# ── /api/models ───────────────────────────────────────────────────────────────


async def _ollama_models(db: Session) -> list:
    """Fetch models from all configured Ollama backends, return UI-shaped dicts."""
    cfg_svc = ProviderConfigService(db)
    ol_cfg = cfg_svc.get_ollama()
    if not ol_cfg.get("ENABLE_OLLAMA_API"):
        return []
    urls: list = ol_cfg.get("OLLAMA_BASE_URLS") or []
    result = []
    for idx, url in enumerate(urls):
        try:
            data = await proxy_json(url.rstrip("/"), "", "GET", "api/tags")
            for m in data.get("models", []):
                model_id = m.get("model") or m.get("name", "")
                result.append(
                    {
                        "id": model_id,
                        "name": m.get("name", model_id),
                        "object": "model",
                        "created": int(_time.time()),
                        "owned_by": "ollama",
                        "ollama": m,
                        "urlIdx": idx,
                    }
                )
        except Exception:
            pass
    return result


async def _openai_models(db: Session) -> list:
    """Fetch models from all configured OpenAI-compatible backends."""
    cfg_svc = ProviderConfigService(db)
    oa_cfg = cfg_svc.get_openai()
    if not oa_cfg.get("ENABLE_OPENAI_API"):
        return []
    urls: list = oa_cfg.get("OPENAI_API_BASE_URLS") or []
    keys: list = oa_cfg.get("OPENAI_API_KEYS") or []
    result = []
    for idx, url in enumerate(urls):
        key = keys[idx] if idx < len(keys) else ""
        try:
            data = await proxy_json(url.rstrip("/"), key, "GET", "models")
            for m in data.get("data", []):
                model_id = m.get("id") or m.get("name", "")
                result.append(
                    {
                        **m,
                        "id": model_id,
                        "name": m.get("name", model_id),
                        "object": "model",
                        "owned_by": "openai",
                        "urlIdx": idx,
                    }
                )
        except Exception:
            pass
    return result


@router.get("/models")
async def get_models(
    base: bool = Query(False),
    user: User = Depends(get_current_user),
    svc: ModelsService = Depends(_models_svc),
    db: Session = Depends(get_db),
):
    """Return merged models from Ollama + OpenAI providers + DB custom models.

    The UI does `res?.data ?? []` so the {data:[...]} envelope is required.
    """
    if base:
        models = svc.list_base()
        return {"data": models if isinstance(models, list) else []}

    # Fetch from upstream providers
    ollama = await _ollama_models(db)
    openai = await _openai_models(db)

    # DB custom/overridden models keyed by base_model_id
    db_models = {
        m.base_model_id: m.to_dict() for m in svc.list_active() if m.base_model_id
    }
    db_standalone = [m.to_dict() for m in svc.list_active() if not m.base_model_id]

    # Merge: provider model gets overridden by matching DB entry
    merged = []
    seen_ids = set()
    for m in ollama + openai:
        mid = m["id"]
        if mid in db_models:
            merged.append({**m, **db_models[mid]})
        else:
            merged.append(m)
        seen_ids.add(mid)

    # Add standalone DB custom models not linked to any provider model
    merged.extend(db_standalone)

    return {"data": merged}


# ── /api/chat/completions ─────────────────────────────────────────────────────

# Fields the UI adds that are NOT part of the OpenAI wire format
_STRIP_FIELDS = {
    "session_id",
    "chat_id",
    "id",
    "params",
    "features",
    "model_item",
    "files",
    "tool_ids",
    "background_tasks",
    "avatar_type",
    "variables",
}

_STANDARD_PARAMS = {
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "seed",
    "repeat_penalty",
    "keep_alive",
    "num_ctx",
    "num_predict",
    "format",
}


def _build_llm_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Strip CE-only fields and hoist `params` into the top-level body."""
    params = body.get("params") or {}
    out = {k: v for k, v in body.items() if k not in _STRIP_FIELDS}
    for k, v in params.items():
        if k in _STANDARD_PARAMS and v is not None:
            out.setdefault(k, v)
    return out


async def _resolve_provider(model_id: str, cfg_svc: ProviderConfigService):
    """Return (base_url, api_key, path_prefix) or raise 404."""
    ol_cfg = cfg_svc.get_ollama()
    if ol_cfg.get("ENABLE_OLLAMA_API"):
        for url in ol_cfg.get("OLLAMA_BASE_URLS") or []:
            try:
                tags = await proxy_json(url.rstrip("/"), "", "GET", "api/tags")
                ids = {m.get("model") or m.get("name") for m in tags.get("models", [])}
                if model_id in ids:
                    return url.rstrip("/"), "", "v1/chat/completions"
            except Exception:
                pass

    oa_cfg = cfg_svc.get_openai()
    if oa_cfg.get("ENABLE_OPENAI_API"):
        urls = oa_cfg.get("OPENAI_API_BASE_URLS") or []
        keys = oa_cfg.get("OPENAI_API_KEYS") or []
        for idx, url in enumerate(urls):
            key = keys[idx] if idx < len(keys) else ""
            try:
                data = await proxy_json(url.rstrip("/"), key, "GET", "models")
                ids = {m.get("id") for m in data.get("data", [])}
                if model_id in ids:
                    return url.rstrip("/"), key, "chat/completions"
            except Exception:
                pass

    raise HTTPException(
        status_code=404,
        detail=f"Model '{model_id}' not found on any configured provider",
    )


async def _stream_to_socket(
    llm_body: Dict[str, Any],
    base_url: str,
    api_key: str,
    path: str,
    user_id: str,
    chat_id: str,
    message_id: str,
) -> None:
    """Stream LLM SSE response as Socket.IO chat-events to the user's room."""
    from gateway.realtime.socket import emit_chat_event

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{base_url}/{path.lstrip('/')}"

    async def _emit(data: dict) -> None:
        await emit_chat_event(
            user_id,
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "data": {"type": "chat:completion", "data": data},
            },
        )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0)
        ) as client:
            async with client.stream(
                "POST", url, json=llm_body, headers=headers
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    await _emit({"error": {"message": response.text}, "done": True})
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    await _emit({**chunk, "done": False})

        await _emit({"done": True, "id": message_id})
    except Exception as exc:
        await _emit({"error": {"message": str(exc)}, "done": True})


@router.post("/chat/completions")
@router.post("/v1/chat/completions")
async def chat_completions(
    body: Dict[str, Any],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unified chat endpoint — routes to Ollama or OpenAI, streams via Socket.IO.

    The HTTP response returns immediately. Token streaming is delivered to the
    browser via Socket.IO `chat-events` (mirrors OpenWebUI socket architecture).
    For non-streaming requests the LLM response is returned directly as JSON.

    Supports both /api/chat/completions and /api/v1/chat/completions.
    """
    from gateway.realtime.socket import SESSION_POOL

    model_id: str = body.get("model", "")
    session_id: str = body.get("session_id", "")
    chat_id: str = body.get("chat_id", "")
    message_id: str = body.get("id", "")
    is_stream: bool = body.get("stream", True)

    cfg_svc = ProviderConfigService(db)
    base_url, api_key, path = await _resolve_provider(model_id, cfg_svc)

    llm_body = _build_llm_body(body)

    if not is_stream:
        return await proxy_json(base_url, api_key, "POST", path, body=llm_body)

    # Resolve socket target: prefer session_id lookup, fall back to user.id
    session_info = SESSION_POOL.get(session_id, {})
    target_user_id = session_info.get("user_id") or user.id

    asyncio.create_task(
        _stream_to_socket(
            llm_body,
            base_url,
            api_key,
            path,
            target_user_id,
            chat_id,
            message_id,
        )
    )

    return {"id": message_id, "object": "chat.completion.start", "model": model_id}


@router.get("/models/base")
def get_base_models(
    user: User = Depends(get_current_user),
    svc: ModelsService = Depends(_models_svc),
):
    models = svc.list_base()
    return {"data": models if isinstance(models, list) else []}


# ── /api/changelog ────────────────────────────────────────────────────────────


@router.get("/changelog")
def get_changelog():
    return {}


# ── /api/version/updates ──────────────────────────────────────────────────────


@router.get("/version/updates")
def get_version_updates():
    return {"current": settings.APP_VERSION, "latest": settings.APP_VERSION}


# ── /api/config/model/filter ─────────────────────────────────────────────────


@router.get("/config/model/filter")
def get_model_filter(user: User = Depends(get_current_user)):
    return {"enabled": False, "models": []}


@router.post("/config/model/filter")
def update_model_filter(body: Dict[str, Any], user: User = Depends(get_current_user)):
    return body


# ── /api/config/models ────────────────────────────────────────────────────────


@router.get("/config/models")
def get_models_config(user: User = Depends(get_current_user)):
    return {"models": []}


@router.post("/config/models")
def update_models_config(body: Dict[str, Any], user: User = Depends(get_current_user)):
    return body


# ── /api/webhook ──────────────────────────────────────────────────────────────


@router.get("/webhook")
def get_webhook(user: User = Depends(get_current_user)):
    return {"url": ""}


@router.post("/webhook")
def update_webhook(body: Dict[str, Any], user: User = Depends(get_current_user)):
    return body


# ── /api/community_sharing ───────────────────────────────────────────────────


@router.get("/community_sharing")
def get_community_sharing(user: User = Depends(get_current_user)):
    return False


@router.post("/community_sharing/toggle")
def toggle_community_sharing(user: User = Depends(get_current_user)):
    return False


# ── /api/tasks ────────────────────────────────────────────────────────────────


@router.post("/tasks/stop/{task_id}")
def stop_task(task_id: str, user: User = Depends(get_current_user)):
    return {"id": task_id, "stopped": True}
