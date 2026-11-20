"""Adaptador dual intercambiable: 100% local (Ollama) o híbrido (API compatible OpenAI)."""
import base64
import json
import os
import re
import requests
from .config import settings
from .pii import mask_pii


def _extract_json_block(raw_text: str) -> dict | None:
    text = raw_text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def ai_status() -> dict:
    mode = settings.ai_provider
    allow_hybrid_img = os.getenv("ALLOW_HYBRID_IMAGE_UPLOAD", "false").lower() == "true"
    info = {
        "provider": mode,
        "pii_masking_enabled": settings.pii_masking_enabled,
        "allow_hybrid_image_upload": allow_hybrid_img,
        "available": False,
        "model": None,
        "detail": "",
    }
    if mode == "disabled":
        info["detail"] = "IA generativa desactivada; operando con reglas deterministas y OCR local."
        return info
    if mode == "local":
        info["model"] = settings.ollama_model
        try:
            r = requests.get(f"{settings.ollama_url}/api/tags", timeout=3)
            if r.ok:
                names = [m.get("name", "") for m in r.json().get("models", [])]
                info["available"] = True
                info["installed_models"] = names
                info["detail"] = f"Ollama local activo en {settings.ollama_url}."
            else:
                info["detail"] = f"Ollama respondió HTTP {r.status_code}."
        except requests.RequestException:
            info["detail"] = f"Ollama no detectado en {settings.ollama_url}. Reglas deterministas y OCR local siguen activos."
        return info
    if mode == "hybrid":
        info["model"] = settings.hybrid_model
        if settings.hybrid_api_url and settings.hybrid_api_key:
            info["available"] = True
            info["detail"] = (
                "Modo híbrido activo con filtro parcial de PII en texto; envío de imágenes bloqueado por defecto."
                if not allow_hybrid_img
                else "Modo híbrido activo (envío de imágenes habilitado: verifique privacidad de documentos)."
            )
        else:
            info["detail"] = "Modo híbrido seleccionado, pero faltan HYBRID_API_URL o HYBRID_API_KEY en .env."
        return info
    info["detail"] = f"AI_PROVIDER desconocido: {mode}. Use local, hybrid o disabled."
    return info


def analyze_json(system_prompt: str, user_prompt: str, timeout: int = 45) -> dict:
    mode = settings.ai_provider
    if mode == "disabled":
        return {"status": "disabled", "provider": mode, "data": None, "pii_redactions": {}}

    prepared_prompt = user_prompt
    pii_counts = {}
    if settings.pii_masking_enabled or mode == "hybrid":
        prepared_prompt, pii_counts = mask_pii(user_prompt)

    if mode == "local":
        try:
            resp = requests.post(
                f"{settings.ollama_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "format": "json",
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prepared_prompt},
                    ],
                },
                timeout=timeout,
            )
            if not resp.ok:
                return {"status": "error", "provider": "local", "detail": f"Ollama HTTP {resp.status_code}", "data": None, "pii_redactions": pii_counts}
            content = resp.json().get("message", {}).get("content", "")
            return {
                "status": "ok",
                "provider": "local",
                "model": settings.ollama_model,
                "data": _extract_json_block(content),
                "pii_redactions": pii_counts,
            }
        except requests.RequestException as exc:
            return {"status": "unavailable", "provider": "local", "detail": str(exc), "data": None, "pii_redactions": pii_counts}

    if mode == "hybrid":
        if not settings.hybrid_api_url or not settings.hybrid_api_key:
            return {"status": "unconfigured", "provider": "hybrid", "detail": "Faltan credenciales híbridas.", "data": None, "pii_redactions": pii_counts}
        try:
            resp = requests.post(
                f"{settings.hybrid_api_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.hybrid_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.hybrid_model,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prepared_prompt},
                    ],
                },
                timeout=timeout,
            )
            if not resp.ok:
                return {"status": "error", "provider": "hybrid", "detail": f"API HTTP {resp.status_code}", "data": None, "pii_redactions": pii_counts}
            content = resp.json()["choices"][0]["message"]["content"]
            return {
                "status": "ok",
                "provider": "hybrid",
                "model": settings.hybrid_model,
                "data": _extract_json_block(content),
                "pii_redactions": pii_counts,
            }
        except requests.RequestException as exc:
            return {"status": "unavailable", "provider": "hybrid", "detail": str(exc), "data": None, "pii_redactions": pii_counts}

    return {"status": "invalid_provider", "provider": mode, "data": None, "pii_redactions": pii_counts}


def vision_ocr_fallback(image_bytes: bytes, prompt: str = "Transcribe fielmente todo el texto visible en español, montos, fechas y RUT.") -> tuple[str | None, str | None]:
    mode = settings.ai_provider
    encoded = base64.b64encode(image_bytes).decode("ascii")
    if mode == "local":
        try:
            resp = requests.post(
                f"{settings.ollama_url}/api/generate",
                json={"model": settings.ollama_vision_model, "prompt": prompt, "images": [encoded], "stream": False},
                timeout=60,
            )
            if resp.ok:
                txt = (resp.json().get("response") or "").strip() or None
                return txt, ("Respaldo de modelo visual local usado; requiere verificación humana." if txt else None)
        except requests.RequestException:
            return None, None
    if mode == "hybrid" and settings.hybrid_api_url and settings.hybrid_api_key:
        allow_img = os.getenv("ALLOW_HYBRID_IMAGE_UPLOAD", "false").lower() == "true"
        if not allow_img:
            return None, "Envío de imagen a proveedor híbrido bloqueado por privacidad (defina ALLOW_HYBRID_IMAGE_UPLOAD=true si autoriza)."
        try:
            resp = requests.post(
                f"{settings.hybrid_api_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.hybrid_api_key}", "Content-Type": "application/json"},
                json={
                    "model": settings.hybrid_model,
                    "temperature": 0.0,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                            ],
                        }
                    ],
                },
                timeout=60,
            )
            if resp.ok:
                txt = (resp.json()["choices"][0]["message"]["content"] or "").strip() or None
                return txt, ("Imagen enviada sin anonimización visual a API híbrida; requiere revisión humana." if txt else None)
        except requests.RequestException:
            return None, None
    return None, None
