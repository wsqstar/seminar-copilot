"""Unified LLM completion: direct OpenAI-compatible API first, dsh headless as fallback.

Configuration (environment variables):
- SEMINAR_LLM_BACKEND: "auto" (default), "api" or "dsh".
- SEMINAR_API_BASE / DEEPSEEK_API_URL: OpenAI-compatible base URL.
- SEMINAR_API_KEY / DEEPSEEK_API_KEY: bearer token.
- SEMINAR_API_MODEL / DEEPSEEK_API_MODEL: chat model id.
- SEMINAR_API_MAX_TOKENS: completion cap (reasoning models consume it too), default 4096.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil

import httpx

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _backend_mode() -> str:
    return os.environ.get("SEMINAR_LLM_BACKEND", "auto").strip().lower() or "auto"


def _api_base() -> str:
    return (
        os.environ.get("SEMINAR_API_BASE")
        or os.environ.get("DEEPSEEK_API_URL")
        or DEFAULT_API_BASE
    ).rstrip("/")


def _api_key() -> str:
    return os.environ.get("SEMINAR_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""


def _api_model() -> str:
    return (
        os.environ.get("SEMINAR_API_MODEL")
        or os.environ.get("DEEPSEEK_API_MODEL")
        or DEFAULT_MODEL
    )


def api_configured() -> bool:
    return bool(_api_key())


def dsh_binary() -> str | None:
    return os.environ.get("SEMINAR_DSH_BIN") or shutil.which("dsh")


def dsh_available() -> bool:
    return bool(dsh_binary())


def llm_available() -> bool:
    mode = _backend_mode()
    if mode == "api":
        return api_configured()
    if mode == "dsh":
        return dsh_available()
    return api_configured() or dsh_available()


async def _complete_api(prompt: str, timeout: float) -> str:
    max_tokens = int(os.environ.get("SEMINAR_API_MAX_TOKENS", "4096"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_api_base()}/chat/completions",
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={
                "model": _api_model(),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"LLM API {response.status_code}: {response.text[:300]}")
    choices = (response.json() or {}).get("choices") or []
    if not choices:
        raise RuntimeError("LLM API returned no choices")
    content = str((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("LLM API returned empty content")
    return content


async def _complete_dsh(prompt: str, timeout: float, cwd: str | None) -> str:
    binary = dsh_binary()
    if not binary:
        raise RuntimeError("no LLM backend available: no API key and no dsh binary")
    env = os.environ.copy()
    env["DSH_PERMISSION_MODE"] = "read-only"
    process = await asyncio.create_subprocess_exec(
        binary,
        "--profile",
        "headless",
        prompt,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("dsh LLM step exceeded its timeout") from None
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"dsh exited with {process.returncode}")
    return stdout.decode("utf-8", errors="replace").strip()


async def complete(prompt: str, timeout: float, cwd: str | None = None) -> str:
    """Return the raw assistant text for a single-turn prompt."""
    mode = _backend_mode()
    if mode == "dsh":
        return await _complete_dsh(prompt, timeout, cwd)
    if api_configured():
        try:
            return await _complete_api(prompt, timeout)
        except Exception:
            if mode == "api" or not dsh_available():
                raise
            # auto mode: fall back to dsh headless on API failure
    elif mode == "api":
        raise RuntimeError("SEMINAR_LLM_BACKEND=api but no API key is configured")
    return await _complete_dsh(prompt, timeout, cwd)


def extract_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("LLM output must be a JSON object")
    return value


async def complete_json(prompt: str, timeout: float, cwd: str | None = None) -> dict:
    return extract_json(await complete(prompt, timeout, cwd))
