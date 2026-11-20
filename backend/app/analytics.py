"""Importación y reglas de revisión. Validación estricta para datos chilenos."""
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
import pandas as pd
from sqlalchemy.orm import Session
from .models import FinancialTransaction, ProcurementProcess, PurchaseOrder, Alert, Supplier

ALIASES = {
    "fecha": "transaction_date", "date": "transaction_date", "fecha_docto": "transaction_date",
    "fecha_documento": "transaction_date", "fecha_contable": "transaction_date",
    "fecha_movimiento": "transaction_date", "tipo": "transaction_type",
    "tipo_movimiento": "transaction_type", "tipo_doc": "transaction_type", "tipo_dte": "transaction_type",
    "monto": "amount_clp", "monto_clp": "amount_clp", "amount": "amount_clp",
    "cargo": "debit_clp", "debe": "debit_clp", "abono": "credit_clp", "haber": "credit_clp",
    "proveedor": "supplier_name", "nombre_proveedor": "supplier_name", "razon_social": "supplier_name",
    "rut_proveedor": "supplier_tax_id", "rut": "supplier_tax_id", "rut_emisor": "supplier_tax_id",
    "factura": "invoice_number", "numero_factura": "invoice_number", "folio": "invoice_number",
    "nro_documento": "invoice_number", "cuenta": "account_code", "codigo_cuenta": "account_code",
    "descripcion": "description", "glosa": "description", "detalle": "description", "concepto": "description",
    "presupuesto": "budget_amount_clp", "presupuesto_clp": "budget_amount_clp",
    "presupuesto_vigente": "budget_amount_clp", "id_licitacion": "external_id",
    "codigo_licitacion": "external_id", "codigo_externo": "external_id",
    "licitacion": "procurement_external_id", "id_proceso_compra": "procurement_external_id",
    "procurement_id": "procurement_external_id", "titulo": "title", "nombre_licitacion": "title",
    "nombre": "title", "fecha_publicacion": "publication_date", "fecha_cierre": "closing_date",
    "estado": "status", "monto_estimado": "estimated_amount_clp", "monto_adjudicado": "awarded_amount_clp",
    "numero_ofertas": "offers_count", "rut_adjudicatario": "awarded_supplier_tax_id",
    "id_orden_compra": "external_id", "orden_compra": "external_id", "fecha_emision": "issue_date",
    "monto_total": "amount_clp",
}


def clean_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "nat"} else text


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    normalized = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in out.columns]
    mapped = [ALIASES.get(c, c) for c in normalized]
    duplicates = sorted({c for c in mapped if mapped.count(c) > 1})
    if duplicates:
        raise ValueError("Encabezados duplicados o en colisión tras normalizar: " + ", ".join(duplicates))
    out.columns = mapped
    return out


def parse_clp(value):
    """Parsea CLP entero; admite número Excel/científico y formatos CL/US inequívocos."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Monto CLP inválido: {value!r}")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError(f"Monto CLP inválido: {value!r}")
        number = Decimal(str(value))
        if number != number.to_integral_value():
            raise ValueError(f"CLP debe ser entero, no se aceptan fracciones: {value!r}")
        return int(number)
    if pd.isna(value) or clean_text(value) == "":
        return None
    s = clean_text(value).replace("$", "").replace("CLP", "").replace("clp", "").replace(" ", "")
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+", s):
        try:
            number = Decimal(s)
            if number != number.to_integral_value():
                raise ValueError(f"CLP debe ser entero, no se aceptan fracciones: {value!r}")
            return int(number)
        except InvalidOperation as exc:
            raise ValueError(f"Monto CLP inválido: {value!r}") from exc
    if not re.fullmatch(r"[+-]?[\d.,]+", s):
        raise ValueError(f"Monto CLP inválido: {value!r}")
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if not s:
        raise ValueError(f"Monto CLP inválido: {value!r}")
    if "." in s and "," in s:
        last_dot, last_comma = s.rfind("."), s.rfind(",")
        dec_sep = "." if last_dot > last_comma else ","
        int_part, frac = s.rsplit(dec_sep, 1)
        if not frac.isdigit() or len(frac) not in (1, 2):
            raise ValueError(f"Formato de monto ambiguo: {value!r}")
        thousands = "," if dec_sep == "." else "."
        if thousands in int_part:
            groups = int_part.split(thousands)
            if not (groups[0].isdigit() and all(len(g) == 3 and g.isdigit() for g in groups[1:])):
                raise ValueError(f"Formato de monto inválido: {value!r}")
        normalized = int_part.replace(thousands, "") + "." + frac
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        groups = s.split(sep)
        if len(groups) > 2:
            if not (groups[0].isdigit() and all(len(g) == 3 and g.isdigit() for g in groups[1:])):
                raise ValueError(f"Formato de monto inválido: {value!r}")
            normalized = "".join(groups)
        elif len(groups) == 2:
            left, right = groups
            if not left.isdigit() or not right.isdigit():
                raise ValueError(f"Monto CLP inválido: {value!r}")
            if len(right) == 3:
                if sep == ",":
                    raise ValueError(
                        f"Formato ambiguo {value!r}; use punto para miles en CLP o escriba el valor sin separador"
                    )
                normalized = left + right
            elif len(right) in (1, 2):
                normalized = left + "." + right
            else:
                raise ValueError(f"Formato de monto ambiguo: {value!r}")
        else:
            normalized = s
    else:
        normalized = s
    try:
        number = Decimal(normalized)
        if number != number.to_integral_value():
            raise ValueError(f"CLP debe ser entero, no se aceptan fracciones: {value!r}")
        return sign * int(number)
    except InvalidOperation as exc:
        raise ValueError(f"Monto CLP inválido: {value!r}") from exc


def parse_chilean_date(value):
    if value is None or pd.isna(value) or clean_text(value) == "":
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.date() if hasattr(value, "date") else value
    s = clean_text(value)
    # Admite ISO con hora (ej. Mercado Público 2026-11-20T15:00:00)
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$", s)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError as exc:
            raise ValueError(f"Fecha inválida: {value!r}") from exc
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", s)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError as exc:
            raise ValueError(f"Fecha inválida: {value!r}") from exc
    raise ValueError(f"Formato de fecha no admitido: {value!r}. Use DD/MM/AAAA o AAAA-MM-DD.")


def normalize_rut(value):
    raw = clean_text(value).upper().replace(".", "").replace(" ", "")
    if not raw:
        return ""
    m = re.fullmatch(r"(\d{6,8})-?([0-9K])", raw)
    if not m:
        raise ValueError(f"RUT inválido: {value!r}")
    body, dv = m.groups()
    factor, total = 2, 0
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    result = 11 - total % 11
    expected = "0" if result == 11 else "K" if result == 10 else str(result)
    if dv != expected:
        raise ValueError(f"Dígito verificador de RUT incorrecto: {value!r}")
    return f"{body}-{dv}"


def read_table(filename: str, raw: bytes) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith((".csv", ".tsv", ".txt")):
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                return pd.read_csv(StringIO(text), sep=None, engine="python", dtype=str, keep_default_na=False)
            except UnicodeDecodeError:
                continue
        raise ValueError("No se pudo decodificar el archivo de texto.")
    if lower.endswith(".xlsx"):
        return pd.read_excel(BytesIO(raw), dtype=object, keep_default_na=False, engine="openpyxl")
    if lower.endswith(".xls"):
        return pd.read_excel(BytesIO(raw), dtype=object, keep_default_na=False, engine="xlrd")
    raise ValueError("Formato no soportado. Utilice CSV, TSV, TXT, XLSX o XLS.")


def _check_lengths(values: dict, limits: dict):
    for field, limit in limits.items():
        value = values.get(field)
        if value is not None and len(str(value)) > limit:
            raise ValueError(f"{field} supera el máximo de {limit} caracteres")


def import_financial(db: Session, tenant_id: str, df: pd.DataFrame, source: str) -> int:
    df = normalize_headers(df)
    if "transaction_date" not in df.columns and "issue_date" in df.columns:
        df = df.rename(columns={"issue_date": "transaction_date"})

    has_debit_credit = "debit_clp" in df.columns or "credit_clp" in df.columns
    if "transaction_date" not in df.columns:
        raise ValueError("Faltan columnas: transaction_date")
    if "amount_clp" not in df.columns and not has_debit_credit:
        raise ValueError("Faltan columnas: amount_clp (o Cargo/Abono, Debe/Haber)")
    if "transaction_type" not in df.columns and not has_debit_credit:
        raise ValueError("Faltan columnas: transaction_type")

    existing_same_source = {
        (tax_id, inv.strip().lower(), amt)
        for tax_id, inv, amt in (
            db.query(
                FinancialTransaction.supplier_tax_id,
                FinancialTransaction.invoice_number,
                FinancialTransaction.amount_clp,
            )
            .filter_by(tenant_id=tenant_id, source_file=source)
            .all()
        )
        if tax_id and inv
    }

    count = 0
    for idx, r in df.iterrows():
        rowno = idx + 2
        try:
            dt = parse_chilean_date(r.get("transaction_date"))
            if "amount_clp" in df.columns and clean_text(r.get("amount_clp")) != "":
                amount = parse_clp(r.get("amount_clp"))
                tx_type = clean_text(r.get("transaction_type"))
            else:
                debit = parse_clp(r.get("debit_clp")) or 0
                credit = parse_clp(r.get("credit_clp")) or 0
                if debit and not credit:
                    amount = abs(debit)
                    tx_type = clean_text(r.get("transaction_type")) or "Cargo / Gasto"
                elif credit and not debit:
                    amount = -abs(credit)
                    tx_type = clean_text(r.get("transaction_type")) or "Abono / Ingreso"
                elif debit or credit:
                    amount = debit - credit
                    tx_type = clean_text(r.get("transaction_type")) or "Movimiento Contable"
                else:
                    amount = None
                    tx_type = clean_text(r.get("transaction_type"))

            if dt is None or amount is None or amount == 0:
                raise ValueError("fecha o monto vacío/cero")
            if not tx_type:
                raise ValueError("tipo de movimiento vacío")
            tax_id = normalize_rut(r.get("supplier_tax_id", ""))
            invoice = clean_text(r.get("invoice_number", ""))
            account = clean_text(r.get("account_code", ""))
            supplier = clean_text(r.get("supplier_name", ""))
            description = clean_text(r.get("description", ""))
            budget = parse_clp(r.get("budget_amount_clp"))
            if budget is not None and budget <= 0:
                raise ValueError("budget_amount_clp debe ser mayor que cero")
            _check_lengths(
                {
                    "transaction_type": tx_type,
                    "account_code": account,
                    "supplier_name": supplier,
                    "supplier_tax_id": tax_id,
                    "invoice_number": invoice,
                    "source_file": source,
                },
                {
                    "transaction_type": 30,
                    "account_code": 80,
                    "supplier_name": 200,
                    "supplier_tax_id": 40,
                    "invoice_number": 100,
                    "source_file": 255,
                },
            )
            key = (tax_id, invoice.strip().lower(), amount)
            if tax_id and invoice and key in existing_same_source:
                continue
            db.add(
                FinancialTransaction(
                    tenant_id=tenant_id,
                    transaction_date=dt,
                    transaction_type=tx_type,
                    account_code=account,
                    supplier_name=supplier,
                    supplier_tax_id=tax_id,
                    invoice_number=invoice,
                    description=description,
                    amount_clp=amount,
                    budget_amount_clp=budget,
                    source_file=source,
                )
            )
            count += 1
        except ValueError as exc:
            raise ValueError(f"Fila {rowno}: {exc}") from exc
    db.commit()
    return count


def import_procurement(db: Session, tenant_id: str, df: pd.DataFrame, source: str) -> dict:
    df = normalize_headers(df)
    if "external_id" not in df or "title" not in df:
        raise ValueError("Licitaciones requiere external_id y title.")
    count = suppliers = updated = 0
    for idx, r in df.iterrows():
        rowno = idx + 2
        try:
            ext, title = clean_text(r.get("external_id")), clean_text(r.get("title"))
            if not ext or not title:
                raise ValueError("identificador y título son obligatorios")
            tax_id = normalize_rut(r.get("awarded_supplier_tax_id", ""))
            status = clean_text(r.get("status")) or "unknown"
            name = clean_text(r.get("supplier_name", ""))
            est = parse_clp(r.get("estimated_amount_clp"))
            awarded = parse_clp(r.get("awarded_amount_clp"))
            offers = parse_clp(r.get("offers_count"))
            if est is not None and est < 0:
                raise ValueError("estimated_amount_clp no puede ser negativo")
            if awarded is not None and awarded < 0:
                raise ValueError("awarded_amount_clp no puede ser negativo")
            if offers is not None and (offers < 0 or offers > 1_000_000):
                raise ValueError("offers_count fuera de rango permitido")
            _check_lengths(
                {
                    "external_id": ext,
                    "title": title,
                    "status": status,
                    "awarded_supplier_tax_id": tax_id,
                    "supplier_name": name,
                    "source": source,
                },
                {
                    "external_id": 100,
                    "title": 500,
                    "status": 80,
                    "awarded_supplier_tax_id": 40,
                    "supplier_name": 250,
                    "source": 80,
                },
            )
            values = dict(
                title=title,
                publication_date=parse_chilean_date(r.get("publication_date")),
                closing_date=parse_chilean_date(r.get("closing_date")),
                status=status,
                estimated_amount_clp=est,
                awarded_amount_clp=awarded,
                offers_count=offers,
                awarded_supplier_tax_id=tax_id,
                source=source,
            )
            row = db.query(ProcurementProcess).filter_by(tenant_id=tenant_id, external_id=ext).first()
            if row:
                if any(getattr(row, k) != v for k, v in values.items()):
                    for k, v in values.items():
                        setattr(row, k, v)
                    updated += 1
            else:
                db.add(ProcurementProcess(tenant_id=tenant_id, external_id=ext, **values))
                count += 1
            if tax_id and name:
                supplier = db.query(Supplier).filter_by(tenant_id=tenant_id, tax_id=tax_id).first()
                if not supplier:
                    db.add(Supplier(tenant_id=tenant_id, tax_id=tax_id, name=name, source=source))
                    suppliers += 1
                elif supplier.name != name:
                    supplier.name = name
        except ValueError as exc:
            raise ValueError(f"Fila {rowno}: {exc}") from exc
    db.commit()
    return {"imported": count, "updated": updated, "suppliers_created": suppliers}


def import_purchase_orders(db: Session, tenant_id: str, df: pd.DataFrame, source: str) -> dict:
    df = normalize_headers(df)
    if not {"external_id", "amount_clp"}.issubset(df.columns):
        raise ValueError("Órdenes de compra requiere external_id y amount_clp.")
    inserted = updated = suppliers = 0
    for idx, r in df.iterrows():
        try:
            ext = clean_text(r.get("external_id"))
            amount = parse_clp(r.get("amount_clp"))
            if not ext or amount is None or amount <= 0:
                raise ValueError("identificador o monto inválido")
            tax = normalize_rut(r.get("supplier_tax_id", ""))
            name = clean_text(r.get("supplier_name", ""))
            values = dict(
                supplier_tax_id=tax,
                supplier_name=name,
                issue_date=parse_chilean_date(r.get("issue_date")),
                amount_clp=amount,
                status=clean_text(r.get("status")) or "unknown",
                procurement_external_id=clean_text(r.get("procurement_external_id", "")),
                source=source,
            )
            _check_lengths(
                {
                    "external_id": ext,
                    "supplier_tax_id": tax,
                    "supplier_name": name,
                    "status": values["status"],
                    "procurement_external_id": values["procurement_external_id"],
                    "source": source,
                },
                {
                    "external_id": 100,
                    "supplier_tax_id": 40,
                    "supplier_name": 250,
                    "status": 80,
                    "procurement_external_id": 100,
                    "source": 80,
                },
            )
            row = db.query(PurchaseOrder).filter_by(tenant_id=tenant_id, external_id=ext).first()
            if row:
                if any(getattr(row, k) != v for k, v in values.items()):
                    for k, v in values.items():
                        setattr(row, k, v)
                    updated += 1
            else:
                db.add(PurchaseOrder(tenant_id=tenant_id, external_id=ext, **values))
                inserted += 1
            if tax and name:
                supplier = db.query(Supplier).filter_by(tenant_id=tenant_id, tax_id=tax).first()
                if not supplier:
                    db.add(Supplier(tenant_id=tenant_id, tax_id=tax, name=name, source=source))
                    suppliers += 1
                elif supplier.name != name:
                    supplier.name = name
        except ValueError as exc:
            raise ValueError(f"Fila {idx + 2}: {exc}") from exc
    db.commit()
    return {"imported": inserted, "updated": updated, "suppliers_created": suppliers}


def _upsert_alert(
    db, cache: dict, tenant_id, domain, record_type, record_id, category, severity, score, title, explanation
) -> int:
    key = (record_type, record_id, category)
    exists = cache.get(key)
    if exists:
        exists.severity = severity
        exists.priority_score = score
        exists.title = title
        exists.explanation = explanation
        exists.is_active = True
        return 0
    alert = Alert(
        tenant_id=tenant_id,
        domain=domain,
        source_record_type=record_type,
        source_record_id=record_id,
        category=category,
        severity=severity,
        priority_score=score,
        title=title,
        explanation=explanation,
        is_active=True,
    )
    db.add(alert)
    cache[key] = alert
    return 1


def run_finance_rules(db: Session, tenant_id: str) -> int:
    existing_alerts = db.query(Alert).filter_by(tenant_id=tenant_id, domain="finance").all()
    cache = {}
    for a in existing_alerts:
        a.is_active = False
        cache[(a.source_record_type, a.source_record_id, a.category)] = a

    rows = db.query(FinancialTransaction).filter_by(tenant_id=tenant_id).all()
    created = 0
    groups = {}
    for t in rows:
        if t.supplier_tax_id and t.invoice_number and t.amount_clp > 0:
            k = (t.supplier_tax_id, t.invoice_number.strip().lower(), t.amount_clp)
            groups.setdefault(k, []).append(t)
        if t.budget_amount_clp and t.budget_amount_clp > 0 and t.amount_clp > t.budget_amount_clp * 1.2:
            ratio = t.amount_clp / t.budget_amount_clp
            created += _upsert_alert(
                db, cache, tenant_id, "finance", "financial_transaction", t.id, "budget_variance",
                "Media" if ratio < 1.5 else "Alta", 65 if ratio < 1.5 else 80,
                "Monto superior al presupuesto de referencia",
                f"El monto equivale a {ratio:.2f} veces el presupuesto informado. Verifique comparabilidad y vigencia.",
            )
    for group in groups.values():
        if len(group) > 1:
            for t in group:
                created += _upsert_alert(
                    db, cache, tenant_id, "finance", "financial_transaction", t.id, "possible_duplicate_invoice",
                    "Alta", 90, "Posible factura duplicada",
                    "Coinciden RUT normalizado, folio y monto. Revise documentos originales y notas de crédito; no es evidencia concluyente.",
                )
    db.commit()
    return created


def run_procurement_rules(db: Session, tenant_id: str) -> int:
    existing_alerts = db.query(Alert).filter_by(tenant_id=tenant_id, domain="procurement").all()
    cache = {}
    for a in existing_alerts:
        a.is_active = False
        cache[(a.source_record_type, a.source_record_id, a.category)] = a

    tenders = db.query(ProcurementProcess).filter_by(tenant_id=tenant_id).all()
    tenders_by_ext = {p.external_id: p for p in tenders}
    created = 0

    for p in tenders:
        if p.offers_count == 1:
            created += _upsert_alert(
                db, cache, tenant_id, "procurement", "procurement_process", p.id, "single_offer",
                "Media", 60, "Proceso con una oferta registrada",
                "Revise bases, mercado, publicidad, plazos y antecedentes antes de extraer conclusiones.",
            )
        if p.offers_count == 0 and p.awarded_amount_clp and p.awarded_amount_clp > 0:
            created += _upsert_alert(
                db, cache, tenant_id, "procurement", "procurement_process", p.id, "award_without_offers",
                "Alta", 85, "Adjudicación sin ofertas registradas",
                "El proceso registra monto adjudicado pero 0 ofertas. Verifique causal de trato directo o calidad del dato.",
            )
        if p.estimated_amount_clp and p.awarded_amount_clp and p.awarded_amount_clp > p.estimated_amount_clp * 1.2:
            created += _upsert_alert(
                db, cache, tenant_id, "procurement", "procurement_process", p.id, "award_over_estimate",
                "Media", 65, "Monto adjudicado superior a estimación",
                "Verifique alcance, impuestos, modificaciones y comparabilidad.",
            )

    po_totals: dict[str, int] = {}
    for po in db.query(PurchaseOrder).filter_by(tenant_id=tenant_id).all():
        if po.procurement_external_id:
            po_totals[po.procurement_external_id] = po_totals.get(po.procurement_external_id, 0) + po.amount_clp
    for ext_id, total_po in po_totals.items():
        tender = tenders_by_ext.get(ext_id)
        if tender and tender.awarded_amount_clp and total_po > tender.awarded_amount_clp * 1.2:
            ratio = total_po / tender.awarded_amount_clp
            created += _upsert_alert(
                db, cache, tenant_id, "procurement", "procurement_process", tender.id, "po_exceeds_award",
                "Alta", 80, "Órdenes de compra superan monto adjudicado",
                f"Las OC asociadas suman {ratio:.2f} veces el monto adjudicado ({total_po:,} CLP). Verifique ampliaciones de contrato.",
            )

    db.commit()
    return created
