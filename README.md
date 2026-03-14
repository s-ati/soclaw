# SOCLAW

**Intelligent Talent Matching Platform**

SOCLAW is a data-driven hiring platform that evaluates candidates across **six key dimensions** to deliver transparent and precise job matching between professionals and organizations.

🌐 Website: https://www.soclaw.tech

---

## Overview

Traditional hiring relies heavily on CVs and subjective evaluation. **SOCLAW** introduces a structured and explainable approach by analyzing both technical qualifications and behavioral signals.

The platform generates a **2-page professional PDF assessment** that helps candidates and employers understand fit before interviews begin.

SOCLAW stands for:

- **S — Skills**
- **O — Ownership**
- **C — Context**
- **L — Location**
- **A — Adaptability**
- **W — Work Style**

---

## Candidate Process

A simple **4-step workflow**:

1. **Create Account** – Sign up using your email  
2. **Upload CV** – Upload your CV and optionally LinkedIn profile (PDF)  
3. **8-Question Profile** – Answer behavioral questions about work style  
4. **Get Your Report** – Receive a 2-page PDF with match score and interview guidance  

---

## SOCLAW Framework

**Skills** – Technical and domain skill matching against job requirements  
**Ownership** – Measures initiative and independence in delivering outcomes  
**Context** – Cultural and organizational environment fit  
**Location** – Remote / hybrid / onsite compatibility  
**Adaptability** – Response to change, feedback, and dynamic environments  
**Work Style** – Communication habits, discipline, and work structure  

---

## Platform Principles

**Transparency**  
All scores are explainable and visible.

**Deterministic Scoring**  
The same inputs always produce the same outputs.

**User Autonomy**  
No exclusivity — users remain free to explore other options.

---

## Tech Stack

Backend:
- Python
- FastAPI

Infrastructure:
- Docker
- Cloud deployment

Features:
- REST API
- PDF report generation
- Candidate evaluation engine

---

## Project Structure

```
soclaw/
├── app/                # FastAPI application
├── Dockerfile
├── Procfile
├── render.yaml
├── requirements.txt
├── runtime.txt
└── run.sh
```

---

## Local Development

Clone the repository:

```bash
git clone https://github.com/yourusername/soclaw.git
cd soclaw
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
bash run.sh
```

Or with FastAPI:

```bash
uvicorn app.main:app --reload
```

API documentation:

```
http://localhost:8000/docs
```

---

## License

© 2026 SOCLAW. All rights reserved.
