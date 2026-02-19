from datetime import datetime, timezone
from sqlalchemy.orm import Session
from .models import CandidateProfile, Assessment


def purge_expired(db: Session) -> int:
    now = datetime.now(timezone.utc)
    n = 0

    expired_assessments = db.query(Assessment).filter(Assessment.expires_at <= now).all()
    for a in expired_assessments:
        # remove pdf file
        try:
            import os
            if a.pdf_path and os.path.exists(a.pdf_path):
                os.remove(a.pdf_path)
        except Exception:
            pass
        db.delete(a)
        n += 1

    expired_profiles = db.query(CandidateProfile).filter(CandidateProfile.expires_at <= now).all()
    for p in expired_profiles:
        db.delete(p)
        n += 1

    db.commit()
    return n
