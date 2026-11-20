import hmac
from fastapi import Header, HTTPException
from .config import settings

_API_KEYS = settings.api_keys()


def require_tenant(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Falta X-API-Key.")
    matched_tenant: str | None = None
    try:
        supplied = x_api_key.encode("utf-8")
        for key, candidate_tenant in _API_KEYS.items():
            if hmac.compare_digest(key.encode("utf-8"), supplied):
                matched_tenant = candidate_tenant
    except (UnicodeError, TypeError):
        matched_tenant = None
    if matched_tenant is None:
        raise HTTPException(status_code=401, detail="Credencial inválida.")
    return matched_tenant
