from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem, PageBreak
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from datetime import datetime, timezone


def risk_bucket(r: float) -> str:
    if r < 25:
        return "Low"
    if r < 50:
        return "Moderate"
    return "Elevated"


def generate_pdf(
    out_path: str,
    candidate_email: str,
    job: dict,
    linkedin_url: str | None,
    extraction: dict,
    questionnaire: dict,
    weights: dict,
    fits: dict,
    risks: dict,
    match_score: int,
    recommendation: str,
    flags: list[str],
    interview_questions: list[str],
    scoring_version: str = "v1.0",
) -> None:
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    normal = styles["Normal"]

    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    elems = []

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # PAGE 1
    elems.append(Paragraph("SpeedMatch — Corporate Internship Risk Assessment (SOCAWL)", h1))
    elems.append(Spacer(1, 0.15*inch))
    elems.append(Paragraph(f"<b>Candidate:</b> {candidate_email}", normal))
    elems.append(Paragraph(f"<b>Role assessed:</b> {job['title']} — {job['company']}", normal))
    elems.append(Paragraph(f"<b>Date:</b> {now} &nbsp;&nbsp; <b>Scoring version:</b> {scoring_version}", normal))
    if linkedin_url:
        elems.append(Paragraph(f"<b>LinkedIn:</b> {linkedin_url}", normal))
    elems.append(Spacer(1, 0.15*inch))

    elems.append(Paragraph(f"<b>Overall Match Score:</b> {match_score}% &nbsp;&nbsp; <b>Recommendation:</b> {recommendation}", h2))
    if flags:
        elems.append(Paragraph(f"<b>Flags:</b> {', '.join(flags)}", normal))
    elems.append(Spacer(1, 0.15*inch))

    # Why this candidate (evidence lists, no snippets)
    elems.append(Paragraph("Why this candidate (evidence from CV/LinkedIn PDFs)", h2))
    bullets = []
    skills = extraction.get("skills", [])
    courses = extraction.get("courses", [])
    projects = extraction.get("projects", [])

    bullets.append(f"Detected skills: {', '.join(skills[:10]) if skills else 'No strong skill signals detected (keyword-based).'}")
    bullets.append(f"Relevant coursework signals: {', '.join(courses[:8]) if courses else 'None detected.'}")
    bullets.append(f"Project signals: {', '.join(projects[:3]) if projects else 'None detected via keyword scan.'}")
    bullets.append(f"Work-style indicators are derived from an 8-question questionnaire (Likert 1–5).")
    bullets.append(f"Document storage: PDFs are deleted immediately after scoring; only structured results are retained for 30 days.")

    elems.append(ListFlowable([ListItem(Paragraph(b, normal)) for b in bullets], bulletType="bullet"))
    elems.append(Spacer(1, 0.2*inch))

    # SOCAWL table with fit, risk, and weights
    elems.append(Paragraph("SOCAWL profile (fit, risk, weights)", h2))
    mapping = [("S","Skill"),("O","Ownership"),("C","Context"),("A","Adaptability"),("W","Work style"),("L","Location")]
    table_data = [["Dimension", "Fit", "Risk", "Risk level", "Weight"]]
    for k, label in mapping:
        fit = round(float(fits[k]), 1)
        risk = round(float(risks[k]), 1)
        table_data.append([label, f"{fit}%", f"{risk}%", risk_bucket(risk), f"{round(weights[k]*100,1)}%"])

    t = Table(table_data, colWidths=[1.6*inch, 0.8*inch, 0.8*inch, 1.0*inch, 0.8*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ]))
    elems.append(t)

    # PAGE 2
    elems.append(PageBreak())
    elems.append(Paragraph("Interview guide (validation prompts)", h1))
    elems.append(Spacer(1, 0.2*inch))
    elems.append(Paragraph("Use these questions to validate the highest predicted risks and confirm strengths.", normal))
    elems.append(Spacer(1, 0.15*inch))

    elems.append(Paragraph("Recommended interview questions (hybrid: 4 static + 3 tailored)", h2))
    elems.append(ListFlowable([ListItem(Paragraph(q, normal)) for q in interview_questions], bulletType="1"))
    elems.append(Spacer(1, 0.2*inch))

    # Disclaimers (corporate pilot)
    elems.append(Paragraph("Methodology and disclaimers", h2))
    disclaimers = [
        "This report estimates forward-looking hiring risks using the SOCAWL framework (Skill, Ownership, Context, Adaptability, Work style, Location).",
        "Scores are normalized (0–100) and computed deterministically from (1) keyword-based document extraction and (2) an 8-question questionnaire.",
        "AI may optionally assist with wording and question tailoring; AI is not used to calculate match scores, weights, or hard-stop logic.",
        "This report does not replace human decision-making. Use it to guide structured interviewing and reduce avoidable screening mistakes.",
    ]
    elems.append(ListFlowable([ListItem(Paragraph(d, normal)) for d in disclaimers], bulletType="bullet"))

    doc.build(elems)
