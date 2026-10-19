"""Relational schema. SQLite by default; any SQLAlchemy URL (e.g. PostgreSQL) works.

Provenance rule: every derived record points back to a `Node` (a located piece of a
source document) or stores the regions it came from.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Case(Base):
    """One procurement case: one tender plus its bidders and reference sources."""
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created: Mapped[datetime] = mapped_column(default=now)


class Bidder(Base):
    __tablename__ = "bidders"
    __table_args__ = (UniqueConstraint("case_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    name: Mapped[str] = mapped_column(String(200))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("case_id", "sha256", "role", "bidder_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    bidder_id: Mapped[int | None] = mapped_column(ForeignKey("bidders.id"))
    role: Mapped[str] = mapped_column(String(20))  # tender | benchmark | bidder
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(500))  # stored copy, named by hash
    kind: Mapped[str] = mapped_column(String(10))  # pdf | xlsx | xls | csv | docx
    status: Mapped[str] = mapped_column(String(20), default="stored")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # pages sizes, sheet names, timings
    created: Mapped[datetime] = mapped_column(default=now)


class Node(Base):
    """A located element of a document tree: section, clause, line, table, row, sheet...

    regions: [{"page": 1, "bbox": [x0, y0, x1, y1]}] in PDF points, top-left origin of the
    page *as displayed* (rotation applied), or [{"sheet": "BoQ1", "row": 13, "col": 13}]
    (1-based) for spreadsheets, or [{"paragraph": 12}] for DOCX.
    """
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))
    kind: Mapped[str] = mapped_column(String(20))
    ref: Mapped[str | None] = mapped_column(String(200))  # clause number, "BoQ1!R13", table row label
    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None]
    regions: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # cells, flags, method, confidence
    seq: Mapped[int] = mapped_column(default=0)  # reading order within the document


class Requirement(Base):
    """Draft / reviewed requirement (editable). Approved ones are frozen into a TemplateVersion."""
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    key: Mapped[str] = mapped_column(String(80))  # stable id, e.g. "GTP-6" or "BQR-3"
    data: Mapped[dict] = mapped_column(JSON)  # schemas.Requirement
    review_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected
    history: Mapped[list] = mapped_column(JSON, default=list)


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("case_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    version: Mapped[int]
    requirements: Mapped[list] = mapped_column(JSON)  # frozen snapshot, never edited
    approved_by: Mapped[str] = mapped_column(String(100))
    created: Mapped[datetime] = mapped_column(default=now)


class Benchmark(Base):
    __tablename__ = "benchmarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))
    data: Mapped[dict] = mapped_column(JSON)  # schemas.BenchmarkRecord
    review_status: Mapped[str] = mapped_column(String(20), default="draft")


class Run(Base):
    """One reproducible evaluation: template version + benchmark snapshot + config."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    template_version_id: Mapped[int] = mapped_column(ForeignKey("template_versions.id"))
    config: Mapped[dict] = mapped_column(JSON)  # rule version, providers, document hashes, benchmark ids
    created: Mapped[datetime] = mapped_column(default=now)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"))
    requirement_key: Mapped[str] = mapped_column(String(80))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))
    data: Mapped[dict] = mapped_column(JSON)  # schemas.Claim


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (UniqueConstraint("run_id", "bidder_id", "requirement_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"))
    requirement_key: Mapped[str] = mapped_column(String(80))
    auto_status: Mapped[str] = mapped_column(String(40))  # produced by rules, never edited
    explanation: Mapped[str] = mapped_column(Text)
    calc: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    final_status: Mapped[str | None] = mapped_column(String(40))  # set only by a human
    reviewer: Mapped[str | None] = mapped_column(String(100))
    review_reason: Mapped[str | None] = mapped_column(Text)


class PriceComparison(Base):
    __tablename__ = "price_comparisons"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("bidders.id"))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))  # bidder's BOQ row
    data: Mapped[dict] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=now)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def make_session_factory(url=None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite:///"):
        config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def audit(s, actor, action, entity, entity_id, **detail):
    s.add(AuditEvent(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=detail))
