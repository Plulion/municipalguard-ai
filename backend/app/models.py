from sqlalchemy import Column, Boolean, Float, String, Integer, BigInteger, Date, DateTime, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from .db import Base

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String(80), primary_key=True)
    name = Column(String(200), nullable=False)
    institution_type = Column(String(80), nullable=False, default="municipality")
    country = Column(String(2), nullable=False, default="CL")
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class FinancialTransaction(Base):
    __tablename__ = "financial_transactions"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    transaction_date = Column(Date, nullable=False, index=True)
    transaction_type = Column(String(30), nullable=False)
    account_code = Column(String(80), nullable=False, default="")
    supplier_name = Column(String(200), nullable=False, default="")
    supplier_tax_id = Column(String(40), nullable=False, default="")
    invoice_number = Column(String(100), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    amount_clp = Column(BigInteger, nullable=False)
    budget_amount_clp = Column(BigInteger)
    source_file = Column(String(255), nullable=False, default="manual")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_fin_tenant_invoice", "tenant_id", "supplier_tax_id", "invoice_number", "amount_clp"),)

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False, default="")
    tax_id = Column(String(40), nullable=False, default="")
    name = Column(String(250), nullable=False)
    source = Column(String(80), nullable=False, default="manual")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "tax_id", name="uq_supplier_tenant_taxid"),)

class ProcurementProcess(Base):
    __tablename__ = "procurement_processes"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False)
    title = Column(String(500), nullable=False)
    publication_date = Column(Date)
    closing_date = Column(Date)
    status = Column(String(80), nullable=False, default="unknown")
    estimated_amount_clp = Column(BigInteger)
    awarded_amount_clp = Column(BigInteger)
    offers_count = Column(Integer)
    awarded_supplier_tax_id = Column(String(40), nullable=False, default="")
    source = Column(String(80), nullable=False, default="manual_csv")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_proc_tenant_external"),)

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False)
    supplier_tax_id = Column(String(40), nullable=False, default="")
    supplier_name = Column(String(250), nullable=False, default="")
    issue_date = Column(Date)
    amount_clp = Column(BigInteger, nullable=False)
    status = Column(String(80), nullable=False, default="unknown")
    procurement_external_id = Column(String(100), nullable=False, default="")
    source = Column(String(80), nullable=False, default="manual_csv")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_po_tenant_external"),)

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    domain = Column(String(40), nullable=False)
    source_record_type = Column(String(60), nullable=False)
    source_record_id = Column(Integer, nullable=True)
    category = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    priority_score = Column(Integer, nullable=False)
    title = Column(String(300), nullable=False)
    explanation = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="Pendiente")
    is_active = Column(Boolean, nullable=False, default=True)
    rule_version = Column(String(30), nullable=False, default="0.3.0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "domain", "source_record_type", "source_record_id", "category", name="uq_alert_dedupe"),)

class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    page_number = Column(Integer)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class AlertAuditEvent(Base):
    __tablename__ = "alert_audit_events"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=False, index=True)
    previous_status = Column(String(30), nullable=False)
    new_status = Column(String(30), nullable=False)
    actor = Column(String(120), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class UploadedArtifact(Base):
    __tablename__ = "uploaded_artifacts"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(120), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(BigInteger, nullable=False)
    storage_path = Column(String(500), nullable=False)
    extraction_method = Column(String(60), nullable=False)
    ocr_confidence = Column(Float, nullable=True)
    needs_visual_review = Column(Boolean, nullable=False, default=False)
    pages_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class TenderPreAudit(Base):
    __tablename__ = "tender_pre_audits"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(80), ForeignKey("tenants.id"), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("uploaded_artifacts.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    bracket = Column(String(10), nullable=True)
    amount_utm = Column(Float, nullable=True)
    ready_to_publish = Column(Boolean, nullable=False, default=False)
    blockers_count = Column(Integer, nullable=False, default=0)
    total_findings = Column(Integer, nullable=False, default=0)
    ai_provider_used = Column(String(40), nullable=False, default="none")
    findings_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
