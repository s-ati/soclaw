from sqlalchemy.orm import Session
from .models import Job, User
from .security import hash_password
from datetime import datetime, timedelta, timezone
from .settings import settings
from .scoring import DEFAULT_WEIGHTS_CORP_INTERN


def seed(db: Session):
    # Admin user
    admin = db.query(User).filter(User.email == "admin@soclaw.local").first()
    if not admin:
        admin = User(
            email="admin@soclaw.local",
            password_hash=hash_password("AdminPassword123!"),
            is_admin=True
        )
        db.add(admin)
        db.commit()

    # Jobs
    if db.query(Job).count() == 0:
        jobs = [
            Job(
                title="Corporate Strategy Intern",
                company="Example Corp",
                location_policy="onsite",
                required_skills=[
                    {"name":"excel","tier":"must","weight":3},
                    {"name":"analysis","tier":"must","weight":3},
                    {"name":"powerpoint","tier":"must","weight":2},
                    {"name":"financial_modeling","tier":"nice","weight":1}
                ],
                nice_to_have_skills=[
                    {"name":"sql","weight":1},
                    {"name":"python","weight":1}
                ],
                context_keywords=["corporate","stakeholder","matrix"],
                weights=DEFAULT_WEIGHTS_CORP_INTERN,
                active=True
            ),
            Job(
                title="Finance Intern",
                company="Example Bank",
                location_policy="hybrid",
                required_skills=[
                    {"name":"excel","tier":"must","weight":3},
                    {"name":"financial_modeling","tier":"must","weight":3},
                    {"name":"analysis","tier":"must","weight":2}
                ],
                nice_to_have_skills=[
                    {"name":"sql","weight":1}
                ],
                context_keywords=["corporate","regulated","stakeholder"],
                weights=DEFAULT_WEIGHTS_CORP_INTERN,
                active=True
            ),
            Job(
                title="Marketing Intern",
                company="BrandVision Agency",
                location_policy="remote",
                required_skills=[
                    {"name":"powerpoint","tier":"must","weight":3},
                    {"name":"analysis","tier":"must","weight":2},
                    {"name":"excel","tier":"nice","weight":1}
                ],
                nice_to_have_skills=[
                    {"name":"python","weight":1}
                ],
                context_keywords=["startup","cross-functional","creative"],
                weights={"S":0.20,"O":0.22,"C":0.10,"L":0.08,"A":0.20,"W":0.20},
                active=True
            ),
            Job(
                title="Data Analytics Intern",
                company="DataFlow Solutions",
                location_policy="hybrid",
                required_skills=[
                    {"name":"python","tier":"must","weight":3},
                    {"name":"sql","tier":"must","weight":3},
                    {"name":"excel","tier":"must","weight":2},
                    {"name":"analysis","tier":"must","weight":2}
                ],
                nice_to_have_skills=[
                    {"name":"financial_modeling","weight":1}
                ],
                context_keywords=["corporate","stakeholder","tech"],
                weights={"S":0.30,"O":0.18,"C":0.12,"L":0.08,"A":0.15,"W":0.17},
                active=True
            ),
        ]
        db.add_all(jobs)
        db.commit()
