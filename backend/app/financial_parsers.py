"""Parsers de formatos financieros y de compras públicas reales de Chile y estándares de auditoría."""
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import zipfile
from defusedxml import ElementTree as ET
import fitz
import pandas as pd
from .ai_provider import analyze_json
from .analytics import ALIASES, clean_text, normalize_rut, parse_chilean_date, parse_clp
from .ingestion import ingest_document

FINANCIAL_ALLOWED_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".tsv", ".txt", ".xml", ".json", ".pdf", ".jpg", ".jpeg", ".png", ".zip",
}
PROCUREMENT_ALLOWED_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".tsv", ".txt", ".json", ".xml",
}

MAX_ZIP_FILES = 200
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

DTE_TYPE_LABELS = {
    "33": ("Factura Electrónica", 1),
    "34": ("Factura Exenta Electrónica", 1),
    "39": ("Boleta Electrónica", 1),
    "41": ("Boleta Exenta Electrónica", 1),
    "46": ("Factura de Compra Electrónica", 1),
    "52": ("Guía de Despacho Electrónica", 1),
    "56": ("Nota de Débito Electrónica", 1),
    "61": ("Nota de Crédito Electrónica", -1),
}

KNOWN_HEADER_TOKENS = set(ALIASES.keys()) | set(ALIASES.values()) | {
    "debit_clp", "credit_clp", "cargo", "abono", "debe", "haber", "fecha_docto", "razon_social", "glosa",
}


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _decode_text_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _detect_header_row(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df.empty:
        return raw_df
    best_idx = 0
    best_score = -1
    max_scan = min(len(raw_df), 12)
    for idx in range(max_scan):
        row_vals = [re.sub(r"\s+", "_", clean_text(v).lower()) for v in raw_df.iloc[idx].tolist()]
        score = sum(1 for v in row_vals if v in KNOWN_HEADER_TOKENS)
        if score > best_score:
            best_score = score
            best_idx = idx
    if best_score < 2:
        best_idx = 0
    headers = [clean_text(v) or f"col_{i}" for i, v in enumerate(raw_df.iloc[best_idx].tolist())]
    data = raw_df.iloc[best_idx + 1:].copy()
    data.columns = headers
    data = data.dropna(how="all").reset_index(drop=True)
    return data


def _read_delimited_text(raw: bytes) -> pd.DataFrame:
    text = _decode_text_bytes(raw)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("El archivo de texto está vacío.")
    sample = "\n".join(lines[:15])
    sep = None
    for candidate in (";", "\t", "|", ","):
        if candidate in lines[0] or sample.count(candidate) >= len(lines[:5]):
            sep = candidate
            break
    df = pd.read_csv(
        StringIO(text),
        sep=sep,
        engine="python",
        dtype=str,
        keep_default_na=False,
        on_bad_lines="skip",
    )
    if len(df.columns) <= 1 and sep is not None:
        df = pd.read_csv(StringIO(text), sep=None, engine="python", dtype=str, keep_default_na=False)
    return df


def _read_excel_smart(filename: str, raw: bytes) -> pd.DataFrame:
    engine = "xlrd" if filename.lower().endswith(".xls") else "openpyxl"
    raw_df = pd.read_excel(BytesIO(raw), header=None, dtype=object, keep_default_na=False, engine=engine)
    return _detect_header_row(raw_df)


def _child_text(elem, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for child in elem:
        if _strip_ns(child.tag).lower() in wanted and child.text:
            return child.text.strip()
    return ""


def _find_descendant_text(elem, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for node in elem.iter():
        if _strip_ns(node.tag).lower() in wanted and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _parse_sii_dte_xml(root) -> list[dict]:
    dte_nodes = [n for n in root.iter() if _strip_ns(n.tag).lower() == "dte"]
    if not dte_nodes and _strip_ns(root.tag).lower() == "documento":
        dte_nodes = [root]
    rows: list[dict] = []
    for dte in dte_nodes:
        doc = next((n for n in dte.iter() if _strip_ns(n.tag).lower() in {"documento", "liquidacion", "exportaciones"}), dte)
        enc = next((n for n in doc.iter() if _strip_ns(n.tag).lower() == "encabezado"), None)
        if enc is None:
            continue
        iddoc = next((n for n in enc if _strip_ns(n.tag).lower() == "iddoc"), None)
        emisor = next((n for n in enc if _strip_ns(n.tag).lower() == "emisor"), None)
        totales = next((n for n in enc if _strip_ns(n.tag).lower() == "totales"), None)
        if iddoc is None or totales is None:
            continue
        tipo = _child_text(iddoc, "TipoDTE")
        folio = _child_text(iddoc, "Folio")
        fch = _child_text(iddoc, "FchEmis")
        rut_emisor = _child_text(emisor, "RUTEmisor") if emisor is not None else ""
        rzn_soc = _child_text(emisor, "RznSoc", "RznSocEmisor") if emisor is not None else ""
        mnt_total = _child_text(totales, "MntTotal", "MntNeto", "MntExe")
        if not fch or not mnt_total:
            continue
        label, sign = DTE_TYPE_LABELS.get(tipo, (f"DTE Tipo {tipo or 'N/A'}", 1))
        amt = abs(parse_clp(mnt_total)) * sign
        detalles = [
            _child_text(det, "NmbItem", "DscItem")
            for det in doc.iter()
            if _strip_ns(det.tag).lower() == "detalle"
        ]
        desc = "; ".join(x for x in detalles if x)[:500] or f"{label} Folio {folio}"
        rows.append({
            "transaction_date": fch,
            "transaction_type": label[:30],
            "supplier_tax_id": rut_emisor,
            "supplier_name": rzn_soc[:200],
            "invoice_number": folio,
            "amount_clp": amt,
            "description": desc,
        })
    return rows


def _parse_sicogen_or_generic_xml(root) -> tuple[list[dict], str]:
    # SICOGEN / Informe Contable CGR o XML contable genérico
    candidate_tags = {
        "movimiento", "asiento", "registro", "linea", "transaccion", "transaction",
        "cuenta", "cuentacontable", "detallemovimiento", "comprobante",
    }
    rows: list[dict] = []
    nodes = [n for n in root.iter() if _strip_ns(n.tag).lower() in candidate_tags and len(list(n)) >= 2]
    is_sicogen = "sicogen" in _strip_ns(root.tag).lower() or any(
        _strip_ns(n.tag).lower() in {"debe", "haber", "codigocuenta", "cuentaagregacion"} for n in root.iter()
    )
    doc_date = _find_descendant_text(root, "fecha", "fechaInforme", "fechaContable", "periodo")

    for node in nodes:
        flat: dict[str, str] = {}
        for child in node:
            key = _strip_ns(child.tag).strip().lower()
            val = (child.text or "").strip()
            if val:
                flat[key] = val
        if not flat:
            continue
        tx_date = (
            flat.get("fecha")
            or flat.get("fechacontable")
            or flat.get("fecha_movimiento")
            or flat.get("transaction_date")
            or doc_date
        )
        account = flat.get("codigocuenta") or flat.get("cuenta") or flat.get("account_code") or ""
        desc = flat.get("glosa") or flat.get("descripcion") or flat.get("denominacion") or flat.get("description") or ""
        rut = flat.get("rut") or flat.get("rutproveedor") or flat.get("rut_proveedor") or ""
        supplier = flat.get("proveedor") or flat.get("razonsocial") or flat.get("nombre_proveedor") or ""
        folio = flat.get("folio") or flat.get("factura") or flat.get("numero_documento") or ""
        budget = flat.get("presupuesto") or flat.get("presupuesto_vigente") or flat.get("budget_amount_clp") or ""

        debe = flat.get("debe") or flat.get("cargo") or ""
        haber = flat.get("haber") or flat.get("abono") or ""
        monto = flat.get("monto") or flat.get("monto_clp") or flat.get("amount_clp") or flat.get("amount") or ""

        if monto:
            amount_val = monto
            tx_type = flat.get("tipo") or flat.get("tipo_movimiento") or ("Contable SICOGEN" if is_sicogen else "Movimiento XML")
        elif debe or haber:
            d_val = parse_clp(debe) or 0
            h_val = parse_clp(haber) or 0
            if d_val and not h_val:
                amount_val = d_val
                tx_type = flat.get("tipo") or "Debe / Cargo"
            elif h_val and not d_val:
                amount_val = -abs(h_val) if is_sicogen else h_val
                tx_type = flat.get("tipo") or "Haber / Abono"
            elif d_val or h_val:
                amount_val = d_val - h_val
                tx_type = flat.get("tipo") or "Neto Contable"
            else:
                continue
        else:
            continue

        if tx_date and amount_val:
            rows.append({
                "transaction_date": tx_date,
                "transaction_type": tx_type[:30],
                "account_code": account[:80],
                "supplier_name": supplier[:200],
                "supplier_tax_id": rut,
                "invoice_number": folio[:100],
                "amount_clp": amount_val,
                "budget_amount_clp": budget or None,
                "description": desc[:1000],
            })
    return rows, ("xml_sicogen" if is_sicogen else "xml_generic")


def _parse_financial_xml(raw: bytes) -> tuple[pd.DataFrame, str]:
    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        raise ValueError(f"XML inválido o bloqueado por seguridad (XXE/DTD): {exc}") from exc

    dte_rows = _parse_sii_dte_xml(root)
    if dte_rows:
        return pd.DataFrame(dte_rows), "xml_sii_dte"

    other_rows, fmt = _parse_sicogen_or_generic_xml(root)
    if other_rows:
        return pd.DataFrame(other_rows), fmt

    raise ValueError("No se encontraron registros financieros reconocibles en el XML (DTE SII, SICOGEN o tabular).")


def _parse_financial_json(raw: bytes) -> pd.DataFrame:
    try:
        data = json.loads(_decode_text_bytes(raw))
    except Exception as exc:
        raise ValueError(f"JSON inválido: {exc}") from exc
    if isinstance(data, dict):
        for key in ("transactions", "movimientos", "data", "items", "records", "Listado"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("El archivo JSON no contiene una lista de movimientos.")
    return pd.json_normalize(data)


def _extract_pdf_tables(raw: bytes) -> pd.DataFrame | None:
    collected: list[pd.DataFrame] = []
    with fitz.open(stream=raw, filetype="pdf") as pdf:
        for page in pdf:
            try:
                finder = page.find_tables()
            except Exception:
                continue
            for tbl in finder.tables:
                rows = tbl.extract()
                if not rows or len(rows) < 2:
                    continue
                candidate = _detect_header_row(pd.DataFrame(rows))
                norm_cols = {re.sub(r"\s+", "_", clean_text(c).lower()) for c in candidate.columns}
                if len(norm_cols & KNOWN_HEADER_TOKENS) >= 2:
                    collected.append(candidate)
    if not collected:
        return None
    return pd.concat(collected, ignore_index=True)


def _extract_single_document_transaction(text: str, filename: str) -> dict | None:
    rut = ""
    for m in re.finditer(r"\b(\d{1,2}\.?\d{3}\.?\d{3}-[0-9Kk])\b", text):
        try:
            rut = normalize_rut(m.group(1))
            break
        except ValueError:
            continue

    tx_date = None
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", text):
        try:
            tx_date = str(parse_chilean_date(m.group(1)))
            break
        except ValueError:
            continue

    folio = ""
    fm = re.search(r"(?:folio|factura|decreto(?:\s+de\s+pago)?|n[°ºo]\.?)\s*[:#-]?\s*([A-Za-z0-9-]{1,25})", text, re.I)
    if fm:
        folio = fm.group(1).strip()

    amount = None
    for pat in (
        r"(?:monto\s*total|total\s*a\s*pagar|total\s*clp|valor\s*total|total)\s*[:$]?\s*\$?\s*([0-9][0-9.,]{2,})",
        r"\$\s*([0-9][0-9.]{2,})",
    ):
        am = re.search(pat, text, re.I)
        if am:
            try:
                amount = parse_clp(am.group(1))
                if amount:
                    break
            except ValueError:
                continue

    lower = text.lower()
    if "nota de crédito" in lower or "nota de credito" in lower:
        tx_type = "Nota de Crédito"
        if amount:
            amount = -abs(amount)
    elif "decreto de pago" in lower:
        tx_type = "Decreto de Pago"
    elif "boleta" in lower:
        tx_type = "Boleta"
    else:
        tx_type = "Factura / Documento"

    if tx_date and amount:
        return {
            "transaction_date": tx_date,
            "transaction_type": tx_type,
            "supplier_tax_id": rut,
            "invoice_number": folio,
            "amount_clp": amount,
            "description": f"Extraído desde {filename}: " + re.sub(r"\s+", " ", text).strip()[:240],
        }
    return None


def _parse_financial_pdf_or_image(tenant_id: str, filename: str, raw: bytes) -> tuple[pd.DataFrame, dict]:
    if filename.lower().endswith(".pdf"):
        table_df = _extract_pdf_tables(raw)
        if table_df is not None and not table_df.empty:
            return table_df, {
                "detected_format": "pdf_table",
                "warnings": ["Tabla extraída automáticamente desde PDF; verifique totales contra cartola o decreto original."],
                "needs_visual_review": True,
            }

    ingested = ingest_document(tenant_id, filename, raw)
    full_text = "\n".join(txt for _, txt in ingested.pages if txt).strip()
    if len(full_text) < 10:
        raise ValueError("No se pudo extraer texto legible del documento financiero (PDF/imagen).")

    row = _extract_single_document_transaction(full_text, filename)
    warnings = list(ingested.warnings)

    if row is None:
        ai_res = analyze_json(
            "Extrae un movimiento financiero chileno en JSON estricto: "
            '{"transaction_date": "YYYY-MM-DD", "transaction_type": "Gasto|Factura|Nota de Crédito", '
            '"supplier_tax_id": "", "supplier_name": "", "invoice_number": "", "amount_clp": 0, "description": ""}.',
            full_text[:6000],
            timeout=30,
        )
        if ai_res.get("status") == "ok" and isinstance(ai_res.get("data"), dict):
            d = ai_res["data"]
            if d.get("transaction_date") and d.get("amount_clp"):
                row = d
                warnings.append("Campos financieros extraídos con asistencia de IA; requiere validación humana obligatoria.")

    if row is None:
        raise ValueError(
            "No se identificaron fecha y monto total claros en el PDF/imagen. "
            "Para cartolas o libros masivos utilice XML (DTE/SICOGEN), XLSX, XLS o CSV."
        )

    warnings.append(f"Extracción desde documento ({ingested.extraction_method}); verifique visualmente monto, folio y RUT.")
    return pd.DataFrame([row]), {
        "detected_format": ingested.extraction_method,
        "ocr_confidence": ingested.ocr_confidence,
        "needs_visual_review": True,
        "warnings": sorted(set(warnings)),
    }


def parse_financial_file(tenant_id: str, filename: str, raw: bytes) -> tuple[pd.DataFrame, dict]:
    ext = Path(filename).suffix.lower()
    if ext not in FINANCIAL_ALLOWED_EXTENSIONS:
        raise ValueError(
            "Formato financiero no soportado. Use XML (DTE SII/SICOGEN), ZIP en lote, XLSX, XLS, CSV, TSV, TXT, JSON, PDF o JPG/PNG."
        )

    if ext == ".zip":
        return _parse_financial_zip(tenant_id, raw)
    if ext == ".xml":
        df, fmt = _parse_financial_xml(raw)
        return df, {"detected_format": fmt, "warnings": [], "needs_visual_review": False}
    if ext == ".json":
        return _parse_financial_json(raw), {"detected_format": "json", "warnings": [], "needs_visual_review": False}
    if ext in {".xlsx", ".xls"}:
        return _read_excel_smart(filename, raw), {"detected_format": ext.lstrip("."), "warnings": [], "needs_visual_review": False}
    if ext in {".csv", ".tsv", ".txt"}:
        return _read_delimited_text(raw), {"detected_format": ext.lstrip("."), "warnings": [], "needs_visual_review": False}
    return _parse_financial_pdf_or_image(tenant_id, filename, raw)


def _parse_financial_zip(tenant_id: str, raw: bytes) -> tuple[pd.DataFrame, dict]:
    if not zipfile.is_zipfile(BytesIO(raw)):
        raise ValueError("El archivo no es un ZIP válido.")
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    formats_seen: set[str] = set()
    total_uncompressed = 0
    processed = 0

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"El ZIP supera el máximo de {MAX_ZIP_FILES} archivos por lote.")
        for info in infos:
            name = info.filename
            if name.startswith("__MACOSX/") or Path(name).name.startswith(".") or ".." in Path(name).parts:
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("El contenido descomprimido del ZIP supera el límite de seguridad de 50 MB.")
            inner_ext = Path(name).suffix.lower()
            if inner_ext == ".zip" or inner_ext not in FINANCIAL_ALLOWED_EXTENSIONS:
                warnings.append(f"Omitido archivo no soportado dentro del ZIP: {name}")
                continue
            inner_bytes = zf.read(info)
            try:
                df, meta = parse_financial_file(tenant_id, Path(name).name, inner_bytes)
                if not df.empty:
                    frames.append(df)
                    formats_seen.add(meta["detected_format"])
                    processed += 1
            except ValueError as exc:
                warnings.append(f"{Path(name).name}: {exc}")

    if not frames:
        raise ValueError("No se pudieron extraer movimientos válidos desde los archivos del ZIP.")
    combined = pd.concat(frames, ignore_index=True).fillna("")
    return combined, {
        "detected_format": f"zip_batch({','.join(sorted(formats_seen))})",
        "files_processed": processed,
        "warnings": warnings[:20],
        "needs_visual_review": any("pdf" in f or "ocr" in f for f in formats_seen),
    }


def _flatten_mercadopublico_items(items: list[dict], kind: str) -> pd.DataFrame:
    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if kind == "tenders" and ("CodigoExterno" in it or "codigoExterno" in it):
            fechas = it.get("Fechas") if isinstance(it.get("Fechas"), dict) else {}
            adj = it.get("Adjudicacion") if isinstance(it.get("Adjudicacion"), dict) else {}
            rows.append({
                "external_id": it.get("CodigoExterno") or it.get("codigoExterno") or "",
                "title": it.get("Nombre") or it.get("nombre") or "",
                "status": it.get("Estado") or str(it.get("CodigoEstado", "unknown")),
                "publication_date": fechas.get("FechaPublicacion") or it.get("FechaPublicacion") or "",
                "closing_date": fechas.get("FechaCierre") or it.get("FechaCierre") or "",
                "estimated_amount_clp": it.get("MontoEstimado"),
                "awarded_amount_clp": adj.get("MontoTotal") or it.get("MontoAdjudicado"),
                "offers_count": adj.get("NumeroOferentes") if adj.get("NumeroOferentes") is not None else it.get("NumeroOfertas"),
                "awarded_supplier_tax_id": adj.get("RutProveedor") or it.get("RutAdjudicatario") or "",
                "supplier_name": adj.get("NombreProveedor") or it.get("NombreProveedor") or "",
            })
        elif kind == "orders" and ("Codigo" in it or "CodigoOrdenCompra" in it):
            fechas = it.get("Fechas") if isinstance(it.get("Fechas"), dict) else {}
            prov = it.get("Proveedor") if isinstance(it.get("Proveedor"), dict) else {}
            rows.append({
                "external_id": it.get("Codigo") or it.get("CodigoOrdenCompra") or "",
                "procurement_external_id": it.get("CodigoLicitacion") or "",
                "status": it.get("Estado") or str(it.get("CodigoEstado", "unknown")),
                "issue_date": fechas.get("FechaCreacion") or it.get("FechaCreacion") or "",
                "amount_clp": it.get("Total") if it.get("Total") is not None else it.get("MontoTotal"),
                "supplier_tax_id": prov.get("RutSucursal") or prov.get("Rut") or it.get("RutProveedor") or "",
                "supplier_name": prov.get("Nombre") or it.get("NombreProveedor") or "",
            })
    return pd.DataFrame(rows) if rows else pd.json_normalize(items)


def parse_procurement_file(filename: str, raw: bytes, kind: str = "tenders") -> tuple[pd.DataFrame, dict]:
    ext = Path(filename).suffix.lower()
    if ext not in PROCUREMENT_ALLOWED_EXTENSIONS:
        raise ValueError("Formato no soportado para compras públicas. Use CSV, XLSX, XLS, TSV, TXT, JSON (Mercado Público) o XML.")
    if ext in {".xlsx", ".xls"}:
        return _read_excel_smart(filename, raw), {"detected_format": ext.lstrip(".")}
    if ext in {".csv", ".tsv", ".txt"}:
        return _read_delimited_text(raw), {"detected_format": ext.lstrip(".")}
    if ext == ".json":
        try:
            data = json.loads(_decode_text_bytes(raw))
        except Exception as exc:
            raise ValueError(f"JSON inválido: {exc}") from exc
        if isinstance(data, dict):
            for key in ("Listado", "licitaciones", "ordenes", "data", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        if not isinstance(data, list) or not data:
            raise ValueError("El JSON no contiene registros de compras públicas.")
        return _flatten_mercadopublico_items(data, kind), {"detected_format": "json_mercadopublico"}

    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        raise ValueError(f"XML inválido o bloqueado por seguridad: {exc}") from exc
    items = []
    for child in root:
        row = {_strip_ns(sub.tag).strip().lower(): (sub.text or "").strip() for sub in child if sub.text and sub.text.strip()}
        if row:
            items.append(row)
    if not items:
        raise ValueError("El XML no contiene registros de compras públicas.")
    return pd.DataFrame(items), {"detected_format": "xml"}
