from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone
from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


# Valid application statuses for the pipeline
APPLICATION_STATUSES = ("saved", "applied", "interview", "offer", "rejected")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profiles = relationship("CandidateProfile", back_populates="user", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="user", cascade="all, delete-orphan")


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="profiles")

    linkedin_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Only structured extraction, no raw text stored
    extraction: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Questionnaire answers Q1..Q8 stored
    questionnaire: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)

    # onsite|hybrid|remote
    location_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="onsite")

    # Example:
    # required_skills: [{"name":"excel","tier":"must","weight":3}, ...]
    required_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    nice_to_have_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    context_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # weights: {"S":0.25,"O":0.20,"W":0.18,"A":0.15,"C":0.12,"L":0.10}
    weights: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="assessments")

    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    scoring_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1.0")

    fits: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risks: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    match_score: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(50), nullable=False)

    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # interview questions stored for reproducibility
    interview_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    pdf_path: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Application(Base):
    """Tracks per-user application status through the pipeline."""
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assessment_id: Mapped[int | None] = mapped_column(ForeignKey("assessments.id"), nullable=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    # saved | applied | interview | offer | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="saved")

    job_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ActivityLog(Base):
    """Simple activity feed per user."""
    __tablename__ = "activity_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # e.g. "report_created", "status_changed", "profile_uploaded"
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
