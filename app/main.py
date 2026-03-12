from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import uuid

from .settings import settings
from .db import Base, engine, get_db
from .models import User, CandidateProfile, Job, Assessment, Application, ActivityLog, APPLICATION_STATUSES
from .security import hash_password, verify_password, create_jwt
from .auth import get_current_user
from .parsing import pdf_to_text, safe_delete
from .extraction import extract_structured
from .scoring import compute_soclaw, DEFAULT_WEIGHTS_CORP_INTERN
from .pdf_report import generate_pdf
from .ai_client import AIClient
from .evidence import evaluate_evidence
from .job_search import search_jobs
from .job_extractor import extract_from_structured_job
from .seed import seed
from .retention import purge_expired

from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title=settings.app_name)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

REPORTS_DIR = Path("./reports")
REPORTS_DIR.mkdir(exist_ok=True)

def utcnow():
    return datetime.now(timezone.utc)

def expires_in_days(days: int) -> datetime:
    return utcnow() + timedelta(days=days)

QUESTION_TEXTS = [
    ("Q1", "I take initiative on tasks without waiting for detailed instructions."),
    ("Q2", "I prefer waiting for explicit guidance before starting a task. (reverse-scored)"),
    ("Q3", "I remain effective when project requirements change unexpectedly."),
    ("Q4", "After receiving critical feedback, I quickly adjust my approach."),
    ("Q5", "I organize my work using clear structure and priorities."),
    ("Q6", "I communicate progress and potential delays early."),
    ("Q7", "I am comfortable operating within structured organizations and formal reporting lines."),
    ("Q8", "I am willing to work primarily onsite if required."),
]

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    # seed admin + jobs
    from .db import SessionLocal
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()

    # retention scheduler
    sched = BackgroundScheduler()
    def _purge():
        from .db import SessionLocal
        db2 = SessionLocal()
        try:
            purge_expired(db2)
        finally:
            db2.close()
    sched.add_job(_purge, "interval", hours=12)
    sched.start()

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})

@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    if len(password) < 8:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Password must be at least 8 characters."})
    if db.query(User).filter(User.email == email_norm).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered."})
    user = User(email=email_norm, password_hash=hash_password(password), is_admin=False)
    db.add(user)
    db.commit()
    return templates.TemplateResponse("register_success.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})
    token = create_jwt(str(user.id))

    # Determine where to send the user based on onboarding state
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).order_by(CandidateProfile.created_at.desc()).first()
    profile_ready = bool(profile and profile.extraction)
    questionnaire_ready = bool(profile and profile.questionnaire and len(profile.questionnaire.keys()) == 8)

    if not profile_ready:
        redirect_to = "/profile/upload"
    elif not questionnaire_ready:
        redirect_to = "/profile/questionnaire"
    else:
        redirect_to = "/dashboard"

    resp = RedirectResponse(redirect_to, status_code=303)
    resp.set_cookie("sm_token", token, httponly=True, samesite="lax")
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("sm_token")
    return resp

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).order_by(CandidateProfile.created_at.desc()).first()
    profile_ready = bool(profile and profile.extraction)
    questionnaire_ready = bool(profile and profile.questionnaire and len(profile.questionnaire.keys()) == 8)

    # Enforce onboarding: must complete profile + questionnaire before dashboard
    if not profile_ready:
        return RedirectResponse("/profile/upload", status_code=303)
    if not questionnaire_ready:
        return RedirectResponse("/profile/questionnaire", status_code=303)

    assessments = db.query(Assessment).filter(Assessment.user_id == user.id).order_by(Assessment.created_at.desc()).limit(10).all()
    has_assessment = len(assessments) > 0
    out = []
    for a in assessments:
        j = db.query(Job).filter(Job.id == a.job_id).first()
        out.append({
            "id": a.id,
            "match_score": a.match_score,
            "recommendation": a.recommendation,
            "job_title": f"{j.title} — {j.company}" if j else f"Job #{a.job_id}",
            "pdf_path": a.pdf_path,
        })

    # Determine current step (1-5)
    if not profile_ready:
        current_step = 1
    elif not questionnaire_ready:
        current_step = 2
    elif not has_assessment:
        current_step = 3
    else:
        current_step = 5

    # --- KPI data ---
    applications = db.query(Application).filter(Application.user_id == user.id).all()

    # Pipeline counts
    pipeline = {s: 0 for s in APPLICATION_STATUSES}
    for app_row in applications:
        if app_row.status in pipeline:
            pipeline[app_row.status] += 1

    kpis = {
        "applications_sent": pipeline.get("applied", 0) + pipeline.get("interview", 0) + pipeline.get("offer", 0) + pipeline.get("rejected", 0),
        "reports_created": len(assessments),
        "interviews": pipeline.get("interview", 0),
        "offers": pipeline.get("offer", 0),
        "rejections": pipeline.get("rejected", 0),
    }

    # Applications table (all, sorted by most recent)
    app_table = []
    for app_row in sorted(applications, key=lambda a: a.updated_at or a.created_at, reverse=True):
        # Find matching assessment for report link
        linked_assessment = None
        if app_row.assessment_id:
            linked_assessment = db.query(Assessment).filter(Assessment.id == app_row.assessment_id).first()
        app_table.append({
            "id": app_row.id,
            "job_title": app_row.job_title,
            "company": app_row.company,
            "match_score": app_row.match_score,
            "status": app_row.status,
            "date": (app_row.updated_at or app_row.created_at).strftime("%b %d, %Y"),
            "assessment_id": linked_assessment.id if linked_assessment else None,
        })

    # Recent activity feed
    activities = db.query(ActivityLog).filter(
        ActivityLog.user_id == user.id
    ).order_by(ActivityLog.created_at.desc()).limit(15).all()
    activity_feed = []
    for act in activities:
        activity_feed.append({
            "action": act.action,
            "detail": act.detail,
            "time": act.created_at.strftime("%b %d, %H:%M"),
        })

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "profile_ready": profile_ready,
        "questionnaire_ready": questionnaire_ready,
        "has_assessment": has_assessment,
        "current_step": current_step,
        "assessments": out,
        "kpis": kpis,
        "pipeline": pipeline,
        "app_table": app_table,
        "activity_feed": activity_feed,
    })

@app.get("/profile/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("upload_profile.html", {"request": request, "message": None, "error": None})

@app.post("/profile/upload", response_class=HTMLResponse)
def upload_post(
    request: Request,
    cv_pdf: UploadFile = File(...),
    linkedin_pdf: UploadFile | None = File(None),
    linkedin_url: str | None = Form(None),
    full_name: str | None = Form(None),
    location: str | None = Form(None),
    availability: str | None = Form(None),
    remote_only: str = Form("no"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Save temp PDFs
    temp_dir = Path("./tmp_uploads")
    temp_dir.mkdir(exist_ok=True)

    cv_path = temp_dir / f"cv_{uuid.uuid4().hex}.pdf"
    with open(cv_path, "wb") as f:
        f.write(cv_pdf.file.read())

    li_path = None
    if linkedin_pdf and linkedin_pdf.filename:
        li_path = temp_dir / f"li_{uuid.uuid4().hex}.pdf"
        with open(li_path, "wb") as f:
            f.write(linkedin_pdf.file.read())

    try:
        cv_text = pdf_to_text(cv_path)
        li_text = pdf_to_text(li_path) if li_path else ""
        combined = (cv_text + "\n" + li_text).strip()

        # Deterministic extraction (always)
        extraction = extract_structured(combined)

    except Exception as e:
        return templates.TemplateResponse("upload_profile.html", {"request": request, "message": None, "error": f"Failed to parse PDF: {e}"})
    finally:
        # Delete PDFs immediately
        safe_delete(cv_path)
        if li_path:
            safe_delete(li_path)

    # store profile (structured only)
    prof = CandidateProfile(
        user_id=user.id,
        linkedin_url=(linkedin_url.strip() if linkedin_url else None),
        extraction=extraction,
        questionnaire={},  # filled later
        created_at=utcnow(),
        expires_at=expires_in_days(settings.retention_days),
    )
    # store metadata in extraction (structured safe)
    prof.extraction["remote_only"] = (remote_only == "yes")
    if full_name and full_name.strip():
        prof.extraction["full_name"] = full_name.strip()
    if location and location.strip():
        prof.extraction["location"] = location.strip()
    if availability and availability.strip():
        prof.extraction["availability"] = availability.strip()

    db.add(prof)
    db.add(ActivityLog(
        user_id=user.id,
        action="profile_uploaded",
        detail="Uploaded CV and profile data",
    ))
    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

@app.get("/profile/questionnaire", response_class=HTMLResponse)
def questionnaire_page(request: Request, user: User = Depends(get_current_user)):
    questions = [{"id": qid, "text": qtext} for qid, qtext in QUESTION_TEXTS]
    return templates.TemplateResponse("questionnaire.html", {"request": request, "questions": questions, "message": None, "error": None})

@app.post("/profile/questionnaire", response_class=HTMLResponse)
def questionnaire_post(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    Q1: int | None = Form(None), Q2: int | None = Form(None),
    Q3: int | None = Form(None), Q4: int | None = Form(None),
    Q5: int | None = Form(None), Q6: int | None = Form(None),
    Q7: int | None = Form(None), Q8: int | None = Form(None),
):
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).order_by(CandidateProfile.created_at.desc()).first()
    questions = [{"id": qid, "text": qtext} for qid, qtext in QUESTION_TEXTS]
    if not profile:
        return templates.TemplateResponse("questionnaire.html", {"request": request, "questions": questions, "message": None, "error": "Upload your CV first."})

    vals = {"Q1":Q1,"Q2":Q2,"Q3":Q3,"Q4":Q4,"Q5":Q5,"Q6":Q6,"Q7":Q7,"Q8":Q8}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return templates.TemplateResponse("questionnaire.html", {"request": request, "questions": questions, "message": None, "error": f"Please answer all questions. Missing: {', '.join(missing)}"})

    profile.questionnaire = vals
    profile.expires_at = expires_in_days(settings.retention_days)
    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.query(Job).filter(Job.active == True).order_by(Job.created_at.desc()).all()
    return templates.TemplateResponse("jobs.html", {
        "request": request, "jobs": jobs, "error": None,
        "search_enabled": True,
    })

@app.get("/api/jobs/search")
async def api_jobs_search(
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    """Search Greenhouse + Lever for matching structured jobs with pagination."""
    result = await search_jobs(q, num=limit, offset=offset)
    return JSONResponse(content=result)

def _normalize_provider_job(raw: str | dict) -> dict | None:
    """Parse and normalize a provider job object from the frontend.

    Handles JSON strings, double-encoded strings, and dict inputs.
    Returns a clean dict with at least a title, or None if invalid.
    """
    import json

    if not raw:
        return None

    provider_job = raw
    # If it's a string, parse JSON (handle double-encoding)
    if isinstance(provider_job, str):
        try:
            provider_job = json.loads(provider_job)
        except (json.JSONDecodeError, TypeError):
            return None
        # Handle double-encoded JSON
        if isinstance(provider_job, str):
            try:
                provider_job = json.loads(provider_job)
            except (json.JSONDecodeError, TypeError):
                return None

    if not isinstance(provider_job, dict):
        return None

    # Must have at least a title
    if not provider_job.get("title"):
        return None

    # Normalize key fields to ensure they exist with correct types
    provider_job.setdefault("company", "Unknown")
    provider_job.setdefault("location", "")
    provider_job.setdefault("work_mode", "onsite")
    provider_job.setdefault("description", "")
    provider_job.setdefault("requirements", "")
    provider_job.setdefault("responsibilities", "")
    provider_job.setdefault("commitment", "full_time")
    provider_job.setdefault("source", "")
    provider_job.setdefault("source_url", "")
    provider_job.setdefault("department", "")

    # Ensure strings
    for key in ["title", "company", "location", "work_mode", "description",
                 "requirements", "responsibilities", "source", "source_url"]:
        if not isinstance(provider_job.get(key), str):
            provider_job[key] = str(provider_job.get(key, "") or "")

    return provider_job


@app.post("/api/jobs/preview")
async def api_jobs_preview(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview a job: validate, extract skills, compute estimated match score.

    Returns a JSON preview with job summary, strengths, gaps, and estimated
    match score — without generating a full PDF report.
    """
    import json

    body = await request.json()
    job_json = body.get("job_json", "")

    provider_job = _normalize_provider_job(job_json)
    if not provider_job:
        return JSONResponse({"error": "Invalid job data. Please try a different job."}, status_code=400)

    # Extract skills and sections
    extracted = extract_from_structured_job(provider_job)

    if not extracted or not extracted.get("quality_sufficient"):
        reason = ", ".join(extracted.get("quality_reasons", [])) if extracted else "insufficient job content"
        return JSONResponse({
            "error": f"This posting has too little content for assessment ({reason}). Try a different job.",
        }, status_code=400)

    # Build job dict for scoring
    job_dict = {
        "title": (extracted.get("title") or provider_job.get("title", ""))[:200],
        "company": (extracted.get("company") or provider_job.get("company", ""))[:200],
        "location_policy": extracted.get("location_policy", "onsite"),
        "required_skills": extracted.get("required_skills", []),
        "nice_to_have_skills": extracted.get("nice_to_have_skills", []),
        "weights": DEFAULT_WEIGHTS_CORP_INTERN,
    }

    # Try to compute estimated match score if user has profile + questionnaire
    profile = db.query(CandidateProfile).filter(
        CandidateProfile.user_id == user.id
    ).order_by(CandidateProfile.created_at.desc()).first()

    estimated_score = None
    recommendation = None
    top_strengths = []
    top_gaps = []

    if profile and profile.extraction and profile.questionnaire and len(profile.questionnaire.keys()) == 8:
        remote_only = bool(profile.extraction.get("remote_only", False))
        res = compute_soclaw(
            extraction=profile.extraction,
            questionnaire=profile.questionnaire,
            linkedin_url=profile.linkedin_url,
            job=job_dict,
            remote_only=remote_only,
        )
        estimated_score = res.match_score
        recommendation = res.recommendation

        # Top strengths: dimensions with highest fit scores
        dim_names = {"S": "Skills", "O": "Ownership", "C": "Context", "L": "Location", "A": "Adaptability", "W": "Work Style"}
        fits_sorted = sorted(res.fits.items(), key=lambda kv: kv[1], reverse=True)
        for dim, val in fits_sorted[:3]:
            if val >= 50:
                top_strengths.append({"dimension": dim_names[dim], "score": round(val, 1)})

        # Top gaps: dimensions with highest risk scores
        risks_sorted = sorted(res.risks.items(), key=lambda kv: kv[1], reverse=True)
        for dim, val in risks_sorted[:3]:
            if val >= 40:
                top_gaps.append({"dimension": dim_names[dim], "score": round(val, 1)})

    # Build key requirements summary
    key_requirements = []
    for s in extracted.get("required_skills", [])[:6]:
        key_requirements.append(s["name"].replace("_", " ").title())
    for s in extracted.get("nice_to_have_skills", [])[:3]:
        key_requirements.append(s["name"].replace("_", " ").title() + " (nice to have)")

    # Short job summary from description
    desc = extracted.get("description", "")
    summary = desc[:300].rsplit(" ", 1)[0] + "..." if len(desc) > 300 else desc

    return JSONResponse({
        "preview": {
            "title": job_dict["title"],
            "company": job_dict["company"],
            "location": provider_job.get("location", ""),
            "work_mode": extracted.get("location_policy", "onsite"),
            "commitment": provider_job.get("commitment", "full_time"),
            "summary": summary,
            "key_requirements": key_requirements,
            "source_url": provider_job.get("source_url", ""),
            "estimated_score": estimated_score,
            "recommendation": recommendation,
            "top_strengths": top_strengths,
            "top_gaps": top_gaps,
            "has_profile": bool(profile and profile.extraction and profile.questionnaire and len(profile.questionnaire.keys()) == 8),
        },
        "error": None,
    })


@app.post("/jobs/from-search", response_class=HTMLResponse)
async def create_job_from_search(
    request: Request,
    job_json: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Receive a validated provider job, create Job record, and start assessment."""

    provider_job = _normalize_provider_job(job_json)

    if not provider_job:
        jobs = db.query(Job).filter(Job.active == True).all()
        return templates.TemplateResponse("jobs.html", {
            "request": request, "jobs": jobs,
            "error": "Invalid job data. Please try selecting a different job.",
            "search_enabled": True,
        })

    # Extract skills and sections from the structured job description
    extracted = extract_from_structured_job(provider_job)

    if extracted and extracted.get("quality_sufficient"):
        job = Job(
            title=(extracted.get("title") or provider_job.get("title", ""))[:200],
            company=(extracted.get("company") or provider_job.get("company", ""))[:200],
            location_policy=extracted.get("location_policy", "onsite"),
            required_skills=extracted.get("required_skills", []),
            nice_to_have_skills=extracted.get("nice_to_have_skills", []),
            context_keywords=extracted.get("context_keywords", []),
            weights=DEFAULT_WEIGHTS_CORP_INTERN,
            source_url=(provider_job.get("source_url") or extracted.get("source_url") or "")[:1024] or None,
            active=True,
        )
    else:
        # Even structured jobs can have thin descriptions — handle gracefully
        jobs = db.query(Job).filter(Job.active == True).all()
        reason = ""
        if extracted and extracted.get("quality_reasons"):
            reason = ", ".join(extracted["quality_reasons"])
        else:
            reason = "insufficient job content"
        return templates.TemplateResponse("jobs.html", {
            "request": request, "jobs": jobs,
            "error": f"This posting has too little content for a reliable assessment ({reason}). Please choose a different job.",
            "search_enabled": True,
        })

    db.add(job)
    db.commit()
    db.refresh(job)

    # Redirect to assessment for this new job
    return RedirectResponse(f"/assess/{job.id}", status_code=307)

@app.post("/assess/{job_id}", response_class=HTMLResponse)
def assess_job(
    request: Request,
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).order_by(CandidateProfile.created_at.desc()).first()
    if not profile or not profile.extraction:
        jobs = db.query(Job).filter(Job.active == True).all()
        return templates.TemplateResponse("jobs.html", {"request": request, "jobs": jobs, "error": "Upload CV first."})
    if not profile.questionnaire or len(profile.questionnaire.keys()) != 8:
        jobs = db.query(Job).filter(Job.active == True).all()
        return templates.TemplateResponse("jobs.html", {"request": request, "jobs": jobs, "error": "Complete questionnaire first."})

    job = db.query(Job).filter(Job.id == job_id, Job.active == True).first()
    if not job:
        return RedirectResponse("/jobs", status_code=303)

    # AI: optional tailored questions (safe)
    ai = AIClient()
    ai_questions = None
    payload = {
        "job": {"title": job.title, "company": job.company, "location_policy": job.location_policy},
        "weights": job.weights or DEFAULT_WEIGHTS_CORP_INTERN,
        "extraction": {k: profile.extraction.get(k) for k in ["skills","courses","projects","context_keywords"]},
        "questionnaire": profile.questionnaire,
    }
    ai_questions = ai.generate_tailored_questions(payload)

    # Deterministic scoring
    job_dict = {
        "title": job.title,
        "company": job.company,
        "location_policy": job.location_policy,
        "required_skills": job.required_skills,
        "nice_to_have_skills": job.nice_to_have_skills,
        "weights": job.weights or DEFAULT_WEIGHTS_CORP_INTERN
    }
    remote_only = bool(profile.extraction.get("remote_only", False))
    res = compute_soclaw(
        extraction=profile.extraction,
        questionnaire=profile.questionnaire,
        linkedin_url=profile.linkedin_url,
        job=job_dict,
        remote_only=remote_only,
        ai_questions=ai_questions,
    )

    # Evidence evaluation pipeline
    evidence_summary = evaluate_evidence(
        extraction=profile.extraction,
        job=job_dict,
        ai_client=ai if ai.enabled else None,
    )

    # Persist assessment + PDF
    pdf_filename = f"soclaw_report_{user.id}_{job.id}_{uuid.uuid4().hex}.pdf"
    pdf_path = str(REPORTS_DIR / pdf_filename)

    # Ensure weights are available for the PDF
    weights = job.weights or DEFAULT_WEIGHTS_CORP_INTERN

    generate_pdf(
        out_path=pdf_path,
        candidate_name=profile.extraction.get("full_name"),
        candidate_email=user.email,
        job=job_dict,
        linkedin_url=profile.linkedin_url,
        extraction=profile.extraction,
        questionnaire=profile.questionnaire,
        weights=weights,
        fits=res.fits,
        risks=res.risks,
        match_score=res.match_score,
        recommendation=res.recommendation,
        flags=res.flags,
        interview_questions=res.interview_questions,
        evidence_summary=evidence_summary,
    )

    assessment = Assessment(
        user_id=user.id,
        profile_id=profile.id,
        job_id=job.id,
        scoring_version="v1.0",
        fits=res.fits,
        risks=res.risks,
        match_score=res.match_score,
        recommendation=res.recommendation,
        flags=res.flags,
        interview_questions=res.interview_questions,
        pdf_path=pdf_path,
        created_at=utcnow(),
        expires_at=expires_in_days(settings.retention_days),
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    # Auto-create Application entry for the pipeline
    existing_app = db.query(Application).filter(
        Application.user_id == user.id,
        Application.job_id == job.id,
    ).first()
    if existing_app:
        existing_app.assessment_id = assessment.id
        existing_app.match_score = res.match_score
    else:
        new_app = Application(
            user_id=user.id,
            assessment_id=assessment.id,
            job_id=job.id,
            status="saved",
            job_title=job.title[:200],
            company=job.company[:200],
            match_score=res.match_score,
        )
        db.add(new_app)

    # Log activity
    db.add(ActivityLog(
        user_id=user.id,
        action="report_created",
        detail=f"Generated report for {job.title} at {job.company} — {res.match_score}% match",
    ))
    db.commit()

    # Format fits/risks for display with full SOCLAW dimension names
    dim_names = {"S": "Skill", "O": "Ownership", "C": "Context", "L": "Location", "A": "Adaptability", "W": "Work style"}
    fits_text = "\n".join(f"  {dim_names.get(k, k)}: {v:.1f}%" for k, v in res.fits.items())
    risks_text = "\n".join(f"  {dim_names.get(k, k)}: {v:.1f}%" for k, v in res.risks.items())

    return templates.TemplateResponse("assessment_result.html", {
        "request": request,
        "match_score": res.match_score,
        "recommendation": res.recommendation,
        "flags": res.flags,
        "assessment_id": assessment.id,
        "fits": fits_text,
        "risks": risks_text,
    })

@app.get("/assessments/{assessment_id}/pdf")
def download_pdf(
    assessment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.user_id == user.id,
    ).first()
    if not assessment or not assessment.pdf_path:
        return RedirectResponse("/dashboard", status_code=303)
    pdf_file = Path(assessment.pdf_path)
    if not pdf_file.exists():
        return RedirectResponse("/dashboard", status_code=303)
    return FileResponse(
        path=str(pdf_file),
        media_type="application/pdf",
        filename=pdf_file.name,
    )

@app.get("/apply/{assessment_id}", response_class=HTMLResponse)
def apply_page(
    request: Request,
    assessment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assessment = db.query(Assessment).filter(
        Assessment.id == assessment_id,
        Assessment.user_id == user.id,
    ).first()
    if not assessment:
        return RedirectResponse("/dashboard", status_code=303)
    job = db.query(Job).filter(Job.id == assessment.job_id).first()
    return templates.TemplateResponse("apply.html", {
        "request": request,
        "assessment": assessment,
        "job": job,
        "user_email": user.email,
    })

@app.post("/api/applications/{app_id}/status")
def update_application_status(
    app_id: int,
    status: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an application's pipeline status."""
    if status not in APPLICATION_STATUSES:
        return JSONResponse({"error": "Invalid status."}, status_code=400)

    app_row = db.query(Application).filter(
        Application.id == app_id,
        Application.user_id == user.id,
    ).first()
    if not app_row:
        return JSONResponse({"error": "Application not found."}, status_code=404)

    old_status = app_row.status
    app_row.status = status
    app_row.updated_at = utcnow()

    db.add(ActivityLog(
        user_id=user.id,
        action="status_changed",
        detail=f"{app_row.job_title} at {app_row.company}: {old_status} → {status}",
    ))
    db.commit()

    return JSONResponse({"ok": True, "new_status": status})
