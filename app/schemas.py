from pydantic import BaseModel, EmailStr, Field
from typing import Literal


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class QuestionnaireIn(BaseModel):
    Q1: int = Field(ge=1, le=5)
    Q2: int = Field(ge=1, le=5)  # reversed
    Q3: int = Field(ge=1, le=5)
    Q4: int = Field(ge=1, le=5)
    Q5: int = Field(ge=1, le=5)
    Q6: int = Field(ge=1, le=5)
    Q7: int = Field(ge=1, le=5)
    Q8: int = Field(ge=1, le=5)


class JobOut(BaseModel):
    id: int
    title: str
    company: str
    location_policy: str


class AssessmentOut(BaseModel):
    id: int
    match_score: int
    recommendation: str
    flags: list
    fits: dict
    risks: dict
