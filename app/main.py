from fastapi import FastAPI, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import uuid

from .settings import settings
from .db import Base, engine, get_db
from .models import User, CandidateProfile, Job, Assessment
from .security import hash_password, verify_password, create_jwt
from .auth import get_current_user
from .parsing import pdf_to_text, safe_delete
from .extraction import extract_structured
from .scoring import compute_soclaw, DEFAULT_WEIGHTS_CORP_INTERN
from .pdf_report import generate_pdf
from .ai_client import AIClient
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
    resp = RedirectResponse("/dashboard", status_code=303)
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

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "profile_ready": profile_ready,
        "questionnaire_ready": questionnaire_ready,
        "has_assessment": has_assessment,
        "current_step": current_step,
        "assessments": out,
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
    if location and location.strip():
        prof.extraction["location"] = location.strip()
    if availability and availability.strip():
        prof.extraction["availability"] = availability.strip()

    db.add(prof)
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
    Q1: int = Form(...), Q2: int = Form(...), Q3: int = Form(...), Q4: int = Form(...),
    Q5: int = Form(...), Q6: int = Form(...), Q7: int = Form(...), Q8: int = Form(...),
):
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).order_by(CandidateProfile.created_at.desc()).first()
    if not profile:
        questions = [{"id": qid, "text": qtext} for qid, qtext in QUESTION_TEXTS]
        return templates.TemplateResponse("questionnaire.html", {"request": request, "questions": questions, "message": None, "error": "Upload your CV first."})

    questionnaire = {"Q1":Q1,"Q2":Q2,"Q3":Q3,"Q4":Q4,"Q5":Q5,"Q6":Q6,"Q7":Q7,"Q8":Q8}
    profile.questionnaire = questionnaire
    profile.expires_at = expires_in_days(settings.retention_days)
    db.commit()

    return RedirectResponse("/dashboard", status_code=303)

@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.query(Job).filter(Job.active == True).order_by(Job.created_at.desc()).all()
    return templates.TemplateResponse("jobs.html", {"request": request, "jobs": jobs, "error": None})

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

    # Persist assessment + PDF
    pdf_filename = f"soclaw_report_{user.id}_{job.id}_{uuid.uuid4().hex}.pdf"
    pdf_path = str(REPORTS_DIR / pdf_filename)

    # Ensure weights are available for the PDF
    weights = job.weights or DEFAULT_WEIGHTS_CORP_INTERN

    generate_pdf(
        out_path=pdf_path,
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
