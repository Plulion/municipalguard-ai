"""Pipeline de ingesta multimodal: PDF nativo, PDF escaneado (OCR), JPG/PNG, DOCX y TXT."""
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from PIL import Image, ImageOps
import fitz
from docx import Document as DocxDocument
import pytesseract
from pytesseract import Output, TesseractNotFoundError
from .ai_provider import vision_ocr_fallback
from .config import settings

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx", ".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class IngestionResult:
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    storage_path: str
    pages: list[tuple[int | None, str]]
    extraction_method: str
    ocr_confidence: float | None = None
    needs_visual_review: bool = False
    warnings: list[str] = field(default_factory=list)


def _detect_magic_mime(filename: str, raw: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Formato no admitido. Use PDF, DOCX, JPG, PNG, WEBP o TXT.")
    if ext == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("El archivo no corresponde a un PDF válido (magic bytes inválidos).")
        return "application/pdf"
    if ext in {".jpg", ".jpeg"}:
        if not raw.startswith(b"\xff\xd8\xff"):
            raise ValueError("El archivo no corresponde a una imagen JPEG válida.")
        return "image/jpeg"
    if ext == ".png":
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("El archivo no corresponde a una imagen PNG válida.")
        return "image/png"
    if ext == ".webp":
        if not (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"):
            raise ValueError("El archivo no corresponde a una imagen WEBP válida.")
        return "image/webp"
    if ext == ".docx":
        if not raw.startswith(b"PK\x03\x04"):
            raise ValueError("El archivo no corresponde a un documento DOCX válido.")
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if b"\x00" in raw[:4096]:
        raise ValueError("El archivo TXT contiene bytes binarios no válidos.")
    return "text/plain"


def _persist_artifact(tenant_id: str, filename: str, sha256: str, raw: bytes) -> str:
    safe_tenant = re.sub(r"[^a-zA-Z0-9_-]", "_", tenant_id)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(filename).name)[:120]
    target_dir = Path(settings.artifacts_dir) / safe_tenant
    os.makedirs(target_dir, exist_ok=True)
    target = target_dir / f"{sha256[:16]}_{safe_name}"
    if not target.exists():
        target.write_bytes(raw)
    return str(target)


def _run_ocr_image(img: Image.Image) -> tuple[str, float, str, list[str]]:
    warnings: list[str] = []
    pre = ImageOps.exif_transpose(img).convert("L")
    pre = ImageOps.autocontrast(pre)
    try:
        try:
            data = pytesseract.image_to_data(pre, lang="spa", output_type=Output.DICT)
        except pytesseract.TesseractError:
            data = pytesseract.image_to_data(pre, lang="eng", output_type=Output.DICT)
            warnings.append("Paquete de idioma español no disponible en Tesseract; se usó inglés.")
        words = []
        confs = []
        for txt, conf in zip(data.get("text", []), data.get("conf", [])):
            token = (txt or "").strip()
            try:
                c = float(conf)
            except (TypeError, ValueError):
                c = -1.0
            if token and c >= 0:
                words.append(token)
                confs.append(c / 100.0)
        text = " ".join(words).strip()
        avg_conf = round(sum(confs) / len(confs), 3) if confs else 0.0
        method = "ocr_tesseract"
    except TesseractNotFoundError:
        text = ""
        avg_conf = 0.0
        method = "ocr_unavailable"
        warnings.append("Tesseract OCR no está instalado en este entorno; use Docker o instale tesseract-ocr.")

    if avg_conf < settings.ocr_min_confidence or len(text) < 20:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        vision_text = vision_ocr_fallback(buf.getvalue())
        if vision_text and len(vision_text) > len(text):
            text = vision_text
            method = f"{method}+vision"
            avg_conf = max(avg_conf, 0.75)
        else:
            warnings.append("Confianza OCR baja o texto escaso; se recomienda verificación visual manual.")
    return text, avg_conf, method, warnings


def ingest_document(tenant_id: str, filename: str, raw: bytes) -> IngestionResult:
    mime_type = _detect_magic_mime(filename, raw)
    sha256 = hashlib.sha256(raw).hexdigest()
    storage_path = _persist_artifact(tenant_id, filename, sha256, raw)
    warnings: list[str] = []

    if mime_type == "text/plain":
        text = raw.decode("utf-8", errors="replace")
        return IngestionResult(
            filename=filename,
            mime_type=mime_type,
            sha256=sha256,
            size_bytes=len(raw),
            storage_path=storage_path,
            pages=[(None, text)],
            extraction_method="text_utf8",
        )

    if mime_type.startswith("image/"):
        with Image.open(io.BytesIO(raw)) as img:
            text, conf, method, w = _run_ocr_image(img)
            warnings.extend(w)
        return IngestionResult(
            filename=filename,
            mime_type=mime_type,
            sha256=sha256,
            size_bytes=len(raw),
            storage_path=storage_path,
            pages=[(1, text)],
            extraction_method=method,
            ocr_confidence=conf,
            needs_visual_review=(conf < settings.ocr_min_confidence),
            warnings=warnings,
        )

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        doc = DocxDocument(io.BytesIO(raw))
        blocks = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        full_text = "\n".join(blocks)
        return IngestionResult(
            filename=filename,
            mime_type=mime_type,
            sha256=sha256,
            size_bytes=len(raw),
            storage_path=storage_path,
            pages=[(1, full_text)],
            extraction_method="docx_native",
        )

    pages: list[tuple[int | None, str]] = []
    ocr_confs: list[float] = []
    used_ocr = False
    used_native = False
    with fitz.open(stream=raw, filetype="pdf") as pdf:
        for i, page in enumerate(pdf):
            native_text = (page.get_text("text") or "").strip()
            if len(native_text) >= 25:
                used_native = True
                pages.append((i + 1, native_text))
            else:
                used_ocr = True
                pix = page.get_pixmap(dpi=220)
                with Image.open(io.BytesIO(pix.tobytes("png"))) as page_img:
                    ocr_text, conf, _m, w = _run_ocr_image(page_img)
                    ocr_confs.append(conf)
                    warnings.extend(w)
                    pages.append((i + 1, ocr_text or native_text))

    avg_conf = round(sum(ocr_confs) / len(ocr_confs), 3) if ocr_confs else None
    if used_native and used_ocr:
        method = "pdf_hybrid_ocr"
    elif used_ocr:
        method = "pdf_scanned_ocr"
    else:
        method = "pdf_native"

    return IngestionResult(
        filename=filename,
        mime_type=mime_type,
        sha256=sha256,
        size_bytes=len(raw),
        storage_path=storage_path,
        pages=pages,
        extraction_method=method,
        ocr_confidence=avg_conf,
        needs_visual_review=(avg_conf is not None and avg_conf < settings.ocr_min_confidence),
        warnings=sorted(set(warnings)),
    )
