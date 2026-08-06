"""Text-to-image generation via OpenRouter's Image API.

  POST {OPENROUTER_BASE_URL}/images
    headers: Authorization: Bearer <OPENROUTER_API_KEY>
    body:    { model, prompt, n, size }
    -> { data: [{ b64_json, media_type }], usage: {...} }

Used by the Capture agent to generate an illustrative/conceptual image and embed it in a
generated deliverable. Returns raw bytes (+ extension) so the caller can both show it to the
model (vision) and write it into the doc-generation workspace.
"""
from __future__ import annotations

import base64
import logging

import httpx

from app.settings import settings
from client.langfuse_client import observe

logger = logging.getLogger(__name__)

_EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
    "image/webp": "webp", "image/gif": "gif",
}


@observe(name="image-gen", as_type="generation")
def generate_image(
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    timeout: float = 120.0,
) -> tuple[bytes, str]:
    """Generate an image from `prompt` via OpenRouter. Returns (image_bytes, extension).

    Raises on any failure (missing key, HTTP error, no image in the response) so the caller can
    surface it to the model instead of embedding a broken image.
    """
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    if not (prompt or "").strip():
        raise ValueError("prompt is empty")

    model = model or settings.IMAGE_GEN_MODEL
    size = size or settings.IMAGE_GEN_SIZE
    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/images"
    body: dict = {"model": model, "prompt": prompt, "n": 1}
    if size:
        body["size"] = size
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()

    items = payload.get("data") or []
    if not items:
        raise RuntimeError(f"OpenRouter returned no image: {str(payload)[:200]}")
    item = items[0]
    ext = _EXT_BY_MIME.get((item.get("media_type") or "image/png").lower(), "png")

    b64 = item.get("b64_json")
    if b64:
        return base64.b64decode(b64), ext
    # Some providers return a URL instead of inline base64 — fetch it.
    dl = item.get("url")
    if dl:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r2 = client.get(dl)
            r2.raise_for_status()
            return r2.content, ext
    raise RuntimeError("OpenRouter image response had neither b64_json nor url")
