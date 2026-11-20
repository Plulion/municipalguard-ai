"""Anonimización básica de datos personales (Ley 21.719) antes de envío a IA."""
import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_CL_RE = re.compile(r"(?:\+?56\s*)?(?:\(?0?9\)?\s*\d{4}[\s-]?\d{4}|\(?0?2\)?\s*\d{4}[\s-]?\d{4})")
PERSONAL_RUT_RE = re.compile(r"\b(?:[1-4]?\d\.\d{3}\.\d{3}|[1-4]?\d{7})-[0-9Kk]\b")


def mask_pii(text: str) -> tuple[str, dict]:
    counts = {"emails": 0, "phones": 0, "personal_ruts": 0}

    def _sub_email(_m):
        counts["emails"] += 1
        return "[CORREO_OCULTO]"

    def _sub_phone(_m):
        counts["phones"] += 1
        return "[TELEFONO_OCULTO]"

    def _sub_rut(_m):
        counts["personal_ruts"] += 1
        return "[RUT_PERSONA_NATURAL]"

    masked = EMAIL_RE.sub(_sub_email, text)
    masked = PHONE_CL_RE.sub(_sub_phone, masked)
    masked = PERSONAL_RUT_RE.sub(_sub_rut, masked)
    return masked, counts
