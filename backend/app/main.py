import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import Base, engine, SessionLocal, get_db
from .models import (
    Tenant,
    FinancialTransaction,
    ProcurementProcess,
    PurchaseOrder,
    Supplier,
    Alert,
    DocumentChunk,
    AlertAuditEvent,
    UploadedArtifact,
    TenderPreAudit,
)
from .schemas import TransactionIn, StatusUpdate, QuestionIn, ProcurementIn
from .security import require_tenant
from .config import settings
from .analytics import (
    import_financial,
    import_procurement,
    import_purchase_orders,
    run_finance_rules,
    run_procurement_rules,
    normalize_rut,
)
from .financial_parsers import (
    FINANCIAL_ALLOWED_EXTENSIONS,
    PROCUREMENT_ALLOWED_EXTENSIONS,
    parse_financial_file,
    parse_procurement_file,
)
from .rag import index_document, retrieve
from .ingestion import ingest_document, ALLOWED_EXTENSIONS
from .ai_provider import ai_status
from .chile_procurement_rules import run_pre_audit_bases, DEFAULT_UTM_CLP


def initialize_demo_tenant():
    db = SessionLocal()
    try:
        profiles = settings.tenant_profiles()
        for tenant_id in set(settings.api_keys().values()):
            if not db.get(Tenant, tenant_id):
                profile = profiles.get(tenant_id, {})
                db.add(
                    Tenant(
                        id=tenant_id,
                        name=profile.get(
                            "name",
                            settings.demo_tenant_name if tenant_id == "demo-municipality" else tenant_id,
                        ),
                        institution_type=profile.get("institution_type", "municipality"),
                        country=profile.get("country", "CL"),
                    )
                )
        db.commit()
    except IntegrityError:
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.replace("sqlite:///", "")
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    os.makedirs(settings.artifacts_dir, exist_ok=True)
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    initialize_demo_tenant()
    yield


app = FastAPI(
    title="MunicipalGuard AI",
    version="0.3.1",
    description="Plataforma multiinstitucional de auditoría financiera (SII DTE, SICOGEN, cartolas), compras públicas y pre-auditoría Ley 21.634.",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("MGAI_CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def tenant_db(tenant_id: str = Depends(require_tenant), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant or not tenant.active:
        raise HTTPException(status_code=403, detail="Institución no habilitada.")
    return tenant_id, db


def read_limited_upload(file: UploadFile) -> bytes:
    if not file.filename:
        raise HTTPException(400, "Archivo sin nombre.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    raw = file.file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(413, f"Archivo supera el límite de {settings.max_upload_mb} MB.")
    return raw


@app.get("/health")
def health():
    return {"status": "ok", "app": "MunicipalGuard AI", "version": "0.3.1", "ai_provider": settings.ai_provider}


@app.get("/formats")
def supported_formats():
    return {
        "finance": sorted(FINANCIAL_ALLOWED_EXTENSIONS),
        "procurement": sorted(PROCUREMENT_ALLOWED_EXTENSIONS),
        "documents_and_preaudit": sorted(ALLOWED_EXTENSIONS),
        "chile_standards": [
            "SII DTE XML (Facturas 33/34, Notas de Crédito 61, Notas de Débito 56, Boletas)",
            "SII RCV CSV (UTF-8 / Latin-1 con separador ';')",
            "CGR SICOGEN Informe Contable XML y TXT",
            "Cartolas Bancarias y ERPs Municipales (XLSX, XLS, CSV, TSV, TXT, PDF)",
            "Mercado Público Datos Abiertos JSON",
            "Lotes masivos ZIP (hasta 200 archivos)",
            "Decretos de Pago y Facturas en PDF/JPG/PNG (con OCR español)",
        ],
    }


@app.get("/ai/status")
def get_ai_status(ctx=Depends(tenant_db)):
    return ai_status()


@app.get("/institution")
def institution(ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    t = db.get(Tenant, tenant_id)
    return {"id": t.id, "name": t.name, "institution_type": t.institution_type, "country": t.country}


@app.get("/summary")
def summary(ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    fin_count, fin_sum = (
        db.query(
            func.count(FinancialTransaction.id),
            func.coalesce(func.sum(FinancialTransaction.amount_clp), 0),
        )
        .filter(FinancialTransaction.tenant_id == tenant_id)
        .one()
    )
    active_alerts = (
        db.query(func.count(Alert.id))
        .filter(Alert.tenant_id == tenant_id, Alert.is_active.is_(True))
        .scalar()
    )
    pending_alerts = (
        db.query(func.count(Alert.id))
        .filter(Alert.tenant_id == tenant_id, Alert.is_active.is_(True), Alert.status == "Pendiente")
        .scalar()
    )
    return {
        "financial_transactions": int(fin_count or 0),
        "financial_amount_clp": int(fin_sum or 0),
        "procurement_processes": db.query(func.count(ProcurementProcess.id)).filter(ProcurementProcess.tenant_id == tenant_id).scalar() or 0,
        "purchase_orders": db.query(func.count(PurchaseOrder.id)).filter(PurchaseOrder.tenant_id == tenant_id).scalar() or 0,
        "suppliers": db.query(func.count(Supplier.id)).filter(Supplier.tenant_id == tenant_id).scalar() or 0,
        "alerts": int(active_alerts or 0),
        "pending_alerts": int(pending_alerts or 0),
        "documents": db.query(func.count(DocumentChunk.id)).filter(DocumentChunk.tenant_id == tenant_id).scalar() or 0,
        "pre_audits": db.query(func.count(TenderPreAudit.id)).filter(TenderPreAudit.tenant_id == tenant_id).scalar() or 0,
    }


@app.post("/finance/transactions/upload")
def upload_finance(file: UploadFile = File(...), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    raw = read_limited_upload(file)
    try:
        df, meta = parse_financial_file(tenant_id, file.filename, raw)
        count = import_financial(db, tenant_id, df, file.filename)
        return {
            "imported": count,
            "filename": file.filename,
            "tenant_id": tenant_id,
            **meta,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(400, detail="No se pudo procesar el archivo financiero. Verifique formato, estructura y contenido.")


@app.post("/finance/transactions")
def create_transaction(payload: TransactionIn, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    values = payload.model_dump()
    if values["amount_clp"] == 0:
        raise HTTPException(400, "El monto CLP no puede ser cero.")
    try:
        values["supplier_tax_id"] = normalize_rut(values.get("supplier_tax_id", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    row = FinancialTransaction(tenant_id=tenant_id, **values, source_file="manual")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@app.get("/finance/transactions")
def list_transactions(limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = (
        db.query(FinancialTransaction)
        .filter_by(tenant_id=tenant_id)
        .order_by(FinancialTransaction.transaction_date.desc(), FinancialTransaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": x.id, "date": str(x.transaction_date), "type": x.transaction_type,
            "account_code": x.account_code, "supplier": x.supplier_name,
            "supplier_tax_id": x.supplier_tax_id, "invoice": x.invoice_number,
            "amount_clp": x.amount_clp, "budget_amount_clp": x.budget_amount_clp,
            "description": x.description, "source_file": x.source_file,
        }
        for x in rows
    ]


@app.post("/procurement/tenders/upload")
def upload_tenders(file: UploadFile = File(...), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    raw = read_limited_upload(file)
    try:
        df, meta = parse_procurement_file(file.filename, raw, kind="tenders")
        result = import_procurement(db, tenant_id, df, file.filename)
        return {**result, **meta, "filename": file.filename}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(400, detail="No se pudo procesar el archivo de licitaciones. Verifique formato y columnas.")


@app.post("/procurement/tenders")
def create_tender(payload: ProcurementIn, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    values = payload.model_dump()
    try:
        values["awarded_supplier_tax_id"] = normalize_rut(values.get("awarded_supplier_tax_id", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    existing = db.query(ProcurementProcess).filter_by(tenant_id=tenant_id, external_id=values["external_id"]).first()
    if existing:
        raise HTTPException(409, "El identificador de licitación ya existe en esta institución.")
    row = ProcurementProcess(tenant_id=tenant_id, **values, source="manual")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@app.get("/procurement/tenders")
def list_tenders(limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = (
        db.query(ProcurementProcess)
        .filter_by(tenant_id=tenant_id)
        .order_by(ProcurementProcess.publication_date.desc(), ProcurementProcess.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": x.id, "external_id": x.external_id, "title": x.title, "status": x.status,
            "publication_date": str(x.publication_date) if x.publication_date else None,
            "closing_date": str(x.closing_date) if x.closing_date else None,
            "estimated_amount_clp": x.estimated_amount_clp, "awarded_amount_clp": x.awarded_amount_clp,
            "offers_count": x.offers_count, "awarded_supplier_tax_id": x.awarded_supplier_tax_id,
        }
        for x in rows
    ]


@app.post("/procurement/orders/upload")
def upload_orders(file: UploadFile = File(...), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    raw = read_limited_upload(file)
    try:
        df, meta = parse_procurement_file(file.filename, raw, kind="orders")
        result = import_purchase_orders(db, tenant_id, df, file.filename)
        return {**result, **meta, "filename": file.filename}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(400, detail="No se pudo procesar el archivo de órdenes de compra. Verifique formato y columnas.")


@app.get("/procurement/orders")
def list_orders(limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = (
        db.query(PurchaseOrder)
        .filter_by(tenant_id=tenant_id)
        .order_by(PurchaseOrder.issue_date.desc(), PurchaseOrder.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": x.id, "external_id": x.external_id, "supplier_tax_id": x.supplier_tax_id,
            "supplier_name": x.supplier_name, "issue_date": str(x.issue_date) if x.issue_date else None,
            "amount_clp": x.amount_clp, "status": x.status, "procurement_external_id": x.procurement_external_id,
        }
        for x in rows
    ]


@app.get("/procurement/suppliers")
def suppliers(limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = db.query(Supplier).filter_by(tenant_id=tenant_id).order_by(Supplier.name).offset(offset).limit(limit).all()
    return [{"id": s.id, "tax_id": s.tax_id, "name": s.name, "source": s.source} for s in rows]


@app.get("/procurement/suppliers/{tax_id}/360")
def supplier_360(tax_id: str, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    try:
        tax_id = normalize_rut(tax_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not tax_id:
        raise HTTPException(400, "RUT inválido o vacío.")
    s = db.query(Supplier).filter_by(tenant_id=tenant_id, tax_id=tax_id).first()
    tenders = db.query(ProcurementProcess).filter_by(tenant_id=tenant_id, awarded_supplier_tax_id=tax_id).all()
    orders = db.query(PurchaseOrder).filter_by(tenant_id=tenant_id, supplier_tax_id=tax_id).all()
    tx_count, tx_total = (
        db.query(func.count(FinancialTransaction.id), func.coalesce(func.sum(FinancialTransaction.amount_clp), 0))
        .filter_by(tenant_id=tenant_id, supplier_tax_id=tax_id)
        .one()
    )
    return {
        "supplier": {"tax_id": tax_id, "name": s.name if s else (orders[0].supplier_name if orders else "Proveedor sin ficha local")},
        "awards_count": len(tenders),
        "purchase_orders_count": len(orders),
        "purchase_orders_total_clp": sum(x.amount_clp for x in orders),
        "financial_transactions_count": int(tx_count or 0),
        "financial_transactions_total_clp": int(tx_total or 0),
        "tenders": [{"external_id": x.external_id, "title": x.title, "awarded_amount_clp": x.awarded_amount_clp} for x in tenders],
        "purchase_orders": [{"external_id": x.external_id, "amount_clp": x.amount_clp, "status": x.status} for x in orders],
    }


@app.post("/analysis/run")
def analyze(ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    finance = run_finance_rules(db, tenant_id)
    procurement = run_procurement_rules(db, tenant_id)
    return {
        "finance_alerts_created": finance,
        "procurement_alerts_created": procurement,
        "message": "Evaluación completada. Las señales requieren revisión humana; no determinan fraude ni responsabilidad.",
    }


@app.get("/alerts")
def list_alerts(
    status: str | None = None,
    domain: str | None = None,
    active_only: bool = True,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    ctx=Depends(tenant_db),
):
    tenant_id, db = ctx
    q = db.query(Alert).filter_by(tenant_id=tenant_id)
    if active_only:
        q = q.filter(Alert.is_active.is_(True))
    if status:
        q = q.filter(Alert.status == status)
    if domain:
        q = q.filter(Alert.domain == domain)
    rows = q.order_by(Alert.priority_score.desc(), Alert.id.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id": a.id, "domain": a.domain, "source_record_type": a.source_record_type,
            "source_record_id": a.source_record_id, "severity": a.severity, "category": a.category,
            "priority_score": a.priority_score, "title": a.title, "explanation": a.explanation,
            "status": a.status, "is_active": a.is_active, "created_at": str(a.created_at),
        }
        for a in rows
    ]


@app.patch("/alerts/{alert_id}")
def update_alert(alert_id: int, payload: StatusUpdate, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    if payload.status not in {"Pendiente", "En revisión", "Cerrada", "Descartada"}:
        raise HTTPException(400, "Estado no permitido.")
    a = db.query(Alert).filter_by(id=alert_id, tenant_id=tenant_id).first()
    if not a:
        raise HTTPException(404, "Alerta no encontrada.")
    previous = a.status
    a.status = payload.status
    db.add(
        AlertAuditEvent(
            tenant_id=tenant_id, alert_id=a.id, previous_status=previous,
            new_status=payload.status, actor=f"api-key:{tenant_id}", reason=payload.reason,
        )
    )
    db.commit()
    return {"id": a.id, "status": a.status, "previous_status": previous, "audit_recorded": True}


@app.get("/alerts/{alert_id}/history")
def alert_history(alert_id: int, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    alert = db.query(Alert).filter_by(id=alert_id, tenant_id=tenant_id).first()
    if not alert:
        raise HTTPException(404, "Alerta no encontrada.")
    events = (
        db.query(AlertAuditEvent)
        .filter_by(alert_id=alert_id, tenant_id=tenant_id)
        .order_by(AlertAuditEvent.created_at.asc())
        .all()
    )
    return [
        {
            "previous_status": e.previous_status, "new_status": e.new_status,
            "actor": e.actor, "reason": e.reason, "created_at": str(e.created_at),
        }
        for e in events
    ]


@app.post("/documents/upload")
def upload_document(file: UploadFile = File(...), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    raw = read_limited_upload(file)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Solo se permiten PDF, DOCX, JPG, PNG, WEBP y TXT.")
    try:
        result = ingest_document(tenant_id, file.filename, raw)
        artifact = UploadedArtifact(
            tenant_id=tenant_id,
            filename=result.filename,
            mime_type=result.mime_type,
            sha256=result.sha256,
            size_bytes=result.size_bytes,
            storage_path=result.storage_path,
            extraction_method=result.extraction_method,
            ocr_confidence=result.ocr_confidence,
            needs_visual_review=result.needs_visual_review,
            pages_count=len(result.pages),
        )
        db.add(artifact)
        db.flush()
        has_text = any(len(text.strip()) >= 15 for _, text in result.pages)
        chunks_count = 0
        if has_text:
            db.query(DocumentChunk).filter_by(tenant_id=tenant_id, filename=file.filename).delete(synchronize_session=False)
            chunks_count = index_document(db, tenant_id, file.filename, result.pages)
        else:
            db.commit()
        return {
            "artifact_id": artifact.id,
            "filename": result.filename,
            "mime_type": result.mime_type,
            "sha256": result.sha256,
            "extraction_method": result.extraction_method,
            "ocr_confidence": result.ocr_confidence,
            "needs_visual_review": result.needs_visual_review,
            "warnings": result.warnings,
            "chunks_indexed": chunks_count,
            "tenant_id": tenant_id,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(400, detail="No se pudo procesar el documento multimodal.")


@app.get("/documents")
def list_documents(limit: int = Query(200, ge=1, le=1000), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = (
        db.query(UploadedArtifact)
        .filter_by(tenant_id=tenant_id)
        .order_by(UploadedArtifact.created_at.desc(), UploadedArtifact.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": x.id,
            "filename": x.filename,
            "mime_type": x.mime_type,
            "sha256": x.sha256[:16],
            "pages": x.pages_count,
            "extraction_method": x.extraction_method,
            "ocr_confidence": x.ocr_confidence,
            "needs_visual_review": x.needs_visual_review,
            "created_at": str(x.created_at),
        }
        for x in rows
    ]


@app.post("/pre-audit/bases")
def pre_audit_bases(
    file: UploadFile = File(...),
    estimated_amount_clp: int | None = Form(default=None),
    utm_value_clp: int = Form(default=DEFAULT_UTM_CLP),
    publication_date: date | None = Form(default=None),
    closing_datetime: datetime | None = Form(default=None),
    criteria_weights_sum: float | None = Form(default=None),
    max_fine_pct: float | None = Form(default=None),
    seriousness_guarantee_pct: float | None = Form(default=None),
    performance_guarantee_pct: float | None = Form(default=None),
    committee_members: int | None = Form(default=None),
    use_ai: bool = Form(default=True),
    ctx=Depends(tenant_db),
):
    tenant_id, db = ctx
    raw = read_limited_upload(file)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Formato no admitido para bases. Use PDF, DOCX, JPG, PNG o TXT.")
    try:
        ingested = ingest_document(tenant_id, file.filename, raw)
        artifact = UploadedArtifact(
            tenant_id=tenant_id,
            filename=ingested.filename,
            mime_type=ingested.mime_type,
            sha256=ingested.sha256,
            size_bytes=ingested.size_bytes,
            storage_path=ingested.storage_path,
            extraction_method=ingested.extraction_method,
            ocr_confidence=ingested.ocr_confidence,
            needs_visual_review=ingested.needs_visual_review,
            pages_count=len(ingested.pages),
        )
        db.add(artifact)
        db.flush()
        full_text = "\n\n".join(text for _, text in ingested.pages if text)
        report = run_pre_audit_bases(
            full_text=full_text,
            estimated_amount_clp=estimated_amount_clp,
            utm_value_clp=utm_value_clp,
            publication_date=publication_date,
            closing_datetime=closing_datetime,
            criteria_weights_sum=criteria_weights_sum,
            max_fine_pct=max_fine_pct,
            seriousness_guarantee_pct=seriousness_guarantee_pct,
            performance_guarantee_pct=performance_guarantee_pct,
            committee_members=committee_members,
            use_ai=use_ai,
        )
        record = TenderPreAudit(
            tenant_id=tenant_id,
            artifact_id=artifact.id,
            filename=ingested.filename,
            bracket=report["bracket"],
            amount_utm=report["amount_utm"],
            ready_to_publish=report["ready_to_publish"],
            blockers_count=report["blockers_count"],
            total_findings=report["total_findings"],
            ai_provider_used=report["ai_review"].get("provider", "none"),
            findings_json=json.dumps(report["findings"], ensure_ascii=False),
        )
        db.add(record)
        db.commit()
        return {
            "pre_audit_id": record.id,
            "artifact_id": artifact.id,
            "filename": ingested.filename,
            "extraction_method": ingested.extraction_method,
            "ocr_confidence": ingested.ocr_confidence,
            "needs_visual_review": ingested.needs_visual_review,
            "warnings": ingested.warnings,
            **report,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, detail=str(exc))
    except Exception:
        db.rollback()
        raise HTTPException(400, detail="No se pudo ejecutar la pre-auditoría de bases.")


@app.get("/pre-audit/bases")
def list_pre_audits(limit: int = Query(100, ge=1, le=500), ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    rows = (
        db.query(TenderPreAudit)
        .filter_by(tenant_id=tenant_id)
        .order_by(TenderPreAudit.created_at.desc(), TenderPreAudit.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": x.id,
            "filename": x.filename,
            "bracket": x.bracket,
            "amount_utm": x.amount_utm,
            "ready_to_publish": x.ready_to_publish,
            "blockers_count": x.blockers_count,
            "total_findings": x.total_findings,
            "ai_provider_used": x.ai_provider_used,
            "findings": json.loads(x.findings_json),
            "created_at": str(x.created_at),
        }
        for x in rows
    ]


@app.post("/rag/ask")
def ask_documents(payload: QuestionIn, ctx=Depends(tenant_db)):
    tenant_id, db = ctx
    sources = retrieve(db, tenant_id, payload.question)
    answer = (
        "No se encontraron fragmentos pertinentes."
        if not sources
        else "Recuperación documental local (TF-IDF). Revise las fuentes originales; esta función no emite conclusiones jurídicas ni sustituye la revisión profesional."
    )
    return {"answer": answer, "sources": sources}
