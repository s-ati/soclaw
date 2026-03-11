"""
SOCLAW Premium PDF Report Generator

Produces a 2-page A4 consulting-grade hiring risk assessment.
Canvas-based rendering for precise layout control.
"""

from reportlab.pdfgen import canvas as canvas_mod
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from xml.sax.saxutils import escape
from datetime import datetime, timezone


# ── Brand palette ─────────────────────────────────────────────

NAVY = colors.HexColor("#1B2A4A")
CHARCOAL = colors.HexColor("#2D3748")
TEAL = colors.HexColor("#0D9488")
TEAL_LIGHT = colors.HexColor("#ECFDF5")
PANEL_BG = colors.HexColor("#F8FAFC")
BORDER = colors.HexColor("#E2E8F0")
BORDER_LIGHT = colors.HexColor("#F1F5F9")
TEXT_PRIMARY = colors.HexColor("#1E293B")
TEXT_BODY = colors.HexColor("#374151")
TEXT_SECONDARY = colors.HexColor("#64748B")
TEXT_MUTED = colors.HexColor("#94A3B8")
WHITE = colors.white

BADGE_LOW_BG = colors.HexColor("#DCFCE7")
BADGE_LOW_FG = colors.HexColor("#166534")
BADGE_MOD_BG = colors.HexColor("#FEF3C7")
BADGE_MOD_FG = colors.HexColor("#92400E")
BADGE_HIGH_BG = colors.HexColor("#FEE2E2")
BADGE_HIGH_FG = colors.HexColor("#991B1B")

BAR_BG = colors.HexColor("#E5E7EB")
BAR_FIT = TEAL
BAR_RISK_LOW = colors.HexColor("#22C55E")
BAR_RISK_MOD = colors.HexColor("#F59E0B")
BAR_RISK_HIGH = colors.HexColor("#EF4444")

# ── Page geometry ─────────────────────────────────────────────

PAGE_W, PAGE_H = A4  # 595.28 x 841.89
ML = 48
MR = 48
MT = 48
MB = 40
CW = PAGE_W - ML - MR  # ~499


# ── Paragraph styles ─────────────────────────────────────────

def _ps(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9, leading=13, textColor=TEXT_BODY)
    base.update(kw)
    return ParagraphStyle(name, **base)


S_BODY = _ps("body")
S_BODY_SM = _ps("body_sm", fontSize=8, leading=11, textColor=TEXT_SECONDARY)
S_BODY_XS = _ps("body_xs", fontSize=7, leading=9.5, textColor=TEXT_MUTED)
S_BULLET = _ps("bullet", fontSize=8.5, leading=12.5)
S_REC_TITLE = _ps("rec_title", fontName="Helvetica-Bold", fontSize=12, leading=15,
                   textColor=NAVY)
S_REC_BODY = _ps("rec_body", fontSize=9, leading=13.5, textColor=TEXT_BODY)
S_EVIDENCE = _ps("evidence", fontSize=7.5, leading=10.5, textColor=TEXT_BODY)
S_METH = _ps("meth", fontSize=7.5, leading=10.5, textColor=TEXT_SECONDARY)
S_QUESTION = _ps("question", fontSize=8.5, leading=12.5, textColor=TEXT_BODY)
S_VALIDATES = _ps("validates", fontSize=7.5, leading=10, textColor=TEXT_SECONDARY,
                   fontName="Helvetica-Oblique")


# ── Dimension metadata ───────────────────────────────────────

DIMENSION_META = {
    "S": ("Skill", "Technical & domain capability"),
    "O": ("Ownership", "Initiative & accountability"),
    "C": ("Context", "Corporate environment readiness"),
    "L": ("Location", "Geographic & policy alignment"),
    "A": ("Adaptability", "Flexibility & learning agility"),
    "W": ("Work Style", "Structure & communication"),
}

DIM_ORDER = ["S", "O", "C", "L", "A", "W"]


# ── Content helpers ───────────────────────────────────────────

def _esc(text: str) -> str:
    return escape(str(text))


def risk_bucket(r: float) -> str:
    if r < 25:
        return "Low"
    if r < 50:
        return "Moderate"
    return "Elevated"


def badge_colors(level: str):
    if level == "Low":
        return BADGE_LOW_BG, BADGE_LOW_FG
    if level == "Moderate":
        return BADGE_MOD_BG, BADGE_MOD_FG
    return BADGE_HIGH_BG, BADGE_HIGH_FG


def bar_color_for_risk(level: str):
    if level == "Low":
        return BAR_RISK_LOW
    if level == "Moderate":
        return BAR_RISK_MOD
    return BAR_RISK_HIGH


def recommendation_copy(rec: str, match_score: int) -> tuple[str, str]:
    if rec == "TOP CANDIDATE":
        return (
            "Strong Proceed",
            "This candidate demonstrates strong alignment across all assessed "
            "dimensions. Advance to structured interview with standard "
            "validation protocols.",
        )
    if rec == "SOLID FIT":
        return (
            "Interview with Targeted Validation",
            "Contextual indicators suggest solid potential. Specific competency "
            "areas warrant focused validation through structured interview "
            "questioning.",
        )
    if rec == "RISK APPLICATION":
        return (
            "Conditional Proceed \u2014 Elevated Screening Required",
            "Multiple risk dimensions exceed acceptable thresholds. Proceed "
            "only with focused technical screening and structured behavioral "
            "validation.",
        )
    return (
        "Do Not Advance",
        "Significant misalignment detected across core assessment dimensions. "
        "This profile does not meet minimum threshold requirements for the "
        "target role.",
    )


def alert_badge_text(flags: list[str], recommendation: str) -> str | None:
    if "critical_skill_gap" in flags:
        return "CRITICAL SKILL GAP"
    if "location_incompatible" in flags:
        return "LOCATION INCOMPATIBLE"
    if "low_ownership" in flags or "low_workstyle" in flags:
        return "MODERATE EXECUTION RISK"
    if recommendation == "SOLID FIT":
        return "CONTEXT STRONG \u00b7 SKILL TO VALIDATE"
    return None


def generate_strengths(fits: dict, extraction: dict) -> list[str]:
    items: list[str] = []
    sorted_dims = sorted(fits.items(), key=lambda kv: kv[1], reverse=True)
    for dim, score in sorted_dims:
        if score >= 60 and len(items) < 3:
            name = DIMENSION_META[dim][0]
            items.append(f"Strong {name.lower()} alignment ({score:.0f}% fit)")
    skills = extraction.get("skills", [])
    if skills and len(items) < 5:
        items.append(f"Relevant skills detected: {', '.join(skills[:5])}")
    courses = extraction.get("courses", [])
    if courses and len(items) < 5:
        items.append(f"Supporting coursework: {', '.join(courses[:3])}")
    projects = extraction.get("projects", [])
    if projects and len(items) < 5:
        items.append(f"Project experience identified ({len(projects)} signal{'s' if len(projects) != 1 else ''})")
    if not items:
        items.append("Limited positive signals detected \u2014 validation recommended")
    return items[:5]


def generate_validation_priorities(risks: dict, flags: list[str]) -> list[str]:
    items: list[str] = []
    if "critical_skill_gap" in flags:
        items.append("Address critical skill gaps through technical assessment")
    if "location_incompatible" in flags:
        items.append("Confirm location and availability constraints")
    sorted_risks = sorted(risks.items(), key=lambda kv: kv[1], reverse=True)
    for dim, score in sorted_risks:
        if score >= 40 and len(items) < 4:
            name = DIMENSION_META[dim][0]
            items.append(f"Validate {name.lower()} \u2014 {score:.0f}% risk exposure")
    if "low_ownership" in flags and not any("ownership" in i.lower() for i in items):
        items.append("Probe ownership and self-direction patterns")
    if not items:
        items.append("No elevated risk dimensions \u2014 standard interview protocol")
    return items[:5]


def parse_interview_question(q: str) -> tuple[str, str]:
    if ": " in q:
        dim, text = q.split(": ", 1)
        return dim.strip(), text.strip()
    return "General", q.strip()


VALIDATES_MAP = {
    "Skill": "Technical problem-solving capability and domain depth",
    "Skill validation": "Specific skill evidence and hands-on proficiency",
    "Ownership": "Self-direction, initiative, and accountability",
    "Ownership validation": "End-to-end delivery without close supervision",
    "Work style": "Structured communication and stakeholder management",
    "Work style validation": "Multi-stakeholder prioritization and conflict handling",
    "Adaptability": "Flexibility under changing requirements",
    "Adaptability validation": "Feedback integration and adaptive decision-making",
    "Context": "Corporate environment readiness and formality",
    "Context validation": "Exposure to formal structures and reporting lines",
    "Location": "On-site or hybrid availability confirmation",
    "Location validation": "Geographic and schedule constraint verification",
    "General": "Overall candidate suitability",
}


# ── Renderer ──────────────────────────────────────────────────

class _Renderer:
    """Stateful canvas renderer that tracks vertical position."""

    def __init__(self, c, data: dict):
        self.c = c
        self.d = data
        self.y = PAGE_H - MT

    # ── drawing primitives ────────────────────────────────────

    def _gap(self, pts: float):
        self.y -= pts

    def _text(self, text, x=None, font="Helvetica", size=9, color=TEXT_BODY):
        x = x if x is not None else ML
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        self.y -= size
        self.c.drawString(x, self.y, text)

    def _text_at(self, text, x, y, font="Helvetica", size=9, color=TEXT_BODY):
        """Draw text at absolute position without moving cursor."""
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        self.c.drawString(x, y, text)

    def _text_right_at(self, text, x, y, font="Helvetica", size=9, color=TEXT_BODY):
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        self.c.drawRightString(x, y, text)

    def _para(self, text, style, x=None, width=None) -> float:
        x = x if x is not None else ML
        width = width if width is not None else CW
        p = Paragraph(text, style)
        _, h = p.wrap(width, 800)
        p.drawOn(self.c, x, self.y - h)
        self.y -= h
        return h

    def _hline(self, color_=BORDER, thickness=0.5, x=None, width=None):
        x = x if x is not None else ML
        width = width if width is not None else CW
        self.c.setStrokeColor(color_)
        self.c.setLineWidth(thickness)
        self.c.line(x, self.y, x + width, self.y)

    def _rect(self, x, y, w, h, fill=PANEL_BG, stroke=None, radius=4):
        self.c.setFillColor(fill)
        if stroke:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(0.5)
            self.c.roundRect(x, y, w, h, radius, fill=1, stroke=1)
        else:
            self.c.roundRect(x, y, w, h, radius, fill=1, stroke=0)

    def _mini_bar(self, x, y, w, h, value, fill_color):
        self.c.setFillColor(BAR_BG)
        self.c.roundRect(x, y, w, h, h / 2, fill=1, stroke=0)
        fw = max(h, w * min(value, 100) / 100)
        self.c.setFillColor(fill_color)
        self.c.roundRect(x, y, fw, h, h / 2, fill=1, stroke=0)

    def _badge(self, x, y, text, bg, fg, font_size=6.5) -> float:
        self.c.setFont("Helvetica-Bold", font_size)
        tw = self.c.stringWidth(text, "Helvetica-Bold", font_size)
        bw = tw + 12
        bh = 14
        self.c.setFillColor(bg)
        self.c.roundRect(x, y, bw, bh, 3, fill=1, stroke=0)
        self.c.setFillColor(fg)
        self.c.drawString(x + 6, y + 4, text)
        return bw

    def _score_gauge(self, cx, cy, radius, score):
        self.c.saveState()
        self.c.setLineCap(1)

        # Background ring
        self.c.setStrokeColor(colors.HexColor("#E5E7EB"))
        self.c.setLineWidth(7)
        self.c.circle(cx, cy, radius, stroke=1, fill=0)

        # Colored arc
        if score > 0:
            if score >= 75:
                arc_color = TEAL
            elif score >= 60:
                arc_color = colors.HexColor("#0EA5E9")
            elif score >= 40:
                arc_color = colors.HexColor("#F59E0B")
            else:
                arc_color = colors.HexColor("#EF4444")
            self.c.setStrokeColor(arc_color)
            self.c.setLineWidth(7)
            extent = -(score / 100) * 360
            self.c.arc(cx - radius, cy - radius,
                       cx + radius, cy + radius, 90, extent)

        self.c.restoreState()

        # Score label
        self.c.setFont("Helvetica-Bold", 20)
        self.c.setFillColor(NAVY)
        self.c.drawCentredString(cx, cy - 6, f"{score}%")
        self.c.setFont("Helvetica", 6.5)
        self.c.setFillColor(TEXT_SECONDARY)
        self.c.drawCentredString(cx, cy - 18, "OVERALL FIT")

    def _bullet_list(self, items, x=None, width=None, style=None):
        x = x if x is not None else ML
        width = width if width is not None else CW
        style = style or S_BULLET
        text_x = x + 12
        text_w = width - 14
        for item in items:
            self.c.setFillColor(TEAL)
            self.c.circle(x + 3, self.y - 5, 1.8, fill=1, stroke=0)
            p = Paragraph(_esc(item), style)
            _, h = p.wrap(text_w, 400)
            p.drawOn(self.c, text_x, self.y - h)
            self.y -= h + 3

    # ── page 1 components ─────────────────────────────────────

    def draw_header(self):
        # Teal accent strip at the very top
        self.c.setFillColor(TEAL)
        self.c.rect(0, PAGE_H - 3, PAGE_W, 3, fill=1, stroke=0)

        self.y = PAGE_H - MT
        self._text("SOCLAW", font="Helvetica-Bold", size=14, color=NAVY)

        # Right-side labels (absolute positioning)
        self._text_right_at("CONFIDENTIAL", PAGE_W - MR, PAGE_H - MT - 6,
                            font="Helvetica", size=7, color=TEXT_MUTED)
        self._text_right_at(f"Scoring {self.d['scoring_version']}",
                            PAGE_W - MR, PAGE_H - MT - 16,
                            font="Helvetica", size=7, color=TEXT_MUTED)

        self._gap(6)
        self._hline(BORDER, 0.5)
        self._gap(14)

    def draw_title_block(self):
        self._text("Corporate Internship Risk Assessment",
                    font="Helvetica-Bold", size=16, color=NAVY)
        self._gap(10)

        name = self.d["candidate_name"]
        role = self.d["job"]["title"]
        company = self.d["job"]["company"]
        date_str = self.d["report_date"]

        # Truncate for display safety
        if len(name) > 35:
            name = name[:34] + "\u2026"
        role_display = f"{role} \u2014 {company}"
        if len(role_display) > 45:
            role_display = role_display[:44] + "\u2026"

        self.c.setFont("Helvetica", 8.5)
        self.c.setFillColor(TEXT_SECONDARY)
        self.y -= 9

        self.c.drawString(ML, self.y, "Candidate:")
        self._text_at(name, ML + 58, self.y,
                       font="Helvetica-Bold", size=8.5, color=TEXT_PRIMARY)

        self._text_at("Role:", ML + 230, self.y,
                       font="Helvetica", size=8.5, color=TEXT_SECONDARY)
        self._text_at(role_display, ML + 258, self.y,
                       font="Helvetica-Bold", size=8.5, color=TEXT_PRIMARY)

        self._text_right_at(f"Date: {date_str}", PAGE_W - MR, self.y,
                             font="Helvetica", size=8.5, color=TEXT_SECONDARY)

        self._gap(16)

    def draw_executive_panel(self):
        panel_h = 108
        panel_top = self.y
        panel_bot = panel_top - panel_h

        # Panel background
        self._rect(ML, panel_bot, CW, panel_h, fill=PANEL_BG, stroke=BORDER,
                    radius=6)

        # Score gauge (left region)
        gauge_cx = ML + 58
        gauge_cy = panel_bot + panel_h / 2
        self._score_gauge(gauge_cx, gauge_cy, 34, self.d["match_score"])

        # Recommendation copy (right region)
        rec_x = ML + 128
        rec_w = CW - 136
        rec_title, rec_summary = recommendation_copy(
            self.d["recommendation"], self.d["match_score"])

        # Title
        p_t = Paragraph(_esc(rec_title), S_REC_TITLE)
        _, th = p_t.wrap(rec_w, 80)
        p_t.drawOn(self.c, rec_x, panel_top - 20 - th)

        # Summary
        p_s = Paragraph(_esc(rec_summary), S_REC_BODY)
        _, sh = p_s.wrap(rec_w, 80)
        p_s.drawOn(self.c, rec_x, panel_top - 20 - th - 6 - sh)

        # Alert badge
        badge_text = alert_badge_text(self.d["flags"], self.d["recommendation"])
        if badge_text:
            badge_y = panel_bot + 10
            if "CRITICAL" in badge_text:
                bg, fg = BADGE_HIGH_BG, BADGE_HIGH_FG
            elif "MODERATE" in badge_text or "LOCATION" in badge_text:
                bg, fg = BADGE_MOD_BG, BADGE_MOD_FG
            else:
                bg, fg = BADGE_MOD_BG, BADGE_MOD_FG
            self._badge(rec_x, badge_y, badge_text, bg, fg, font_size=6)

        self.y = panel_bot
        self._gap(18)

    def draw_takeaways(self):
        self._text("Key Takeaways", font="Helvetica-Bold", size=11, color=NAVY)
        self._gap(10)

        col_w = (CW - 24) / 2
        left_x = ML
        right_x = ML + col_w + 24
        save_y = self.y

        # Left column: Strengths
        self.c.setFont("Helvetica-Bold", 8)
        self.c.setFillColor(TEAL)
        self.y -= 8
        self.c.drawString(left_x, self.y, "STRENGTHS")
        self._gap(8)
        strengths = generate_strengths(self.d["fits"], self.d["extraction"])
        self._bullet_list(strengths, x=left_x, width=col_w)
        left_end = self.y

        # Right column: Validation Priorities
        self.y = save_y
        self.c.setFont("Helvetica-Bold", 8)
        self.c.setFillColor(colors.HexColor("#DC2626"))
        self.y -= 8
        self.c.drawString(right_x, self.y, "VALIDATION PRIORITIES")
        self._gap(8)
        priorities = generate_validation_priorities(
            self.d["risks"], self.d["flags"])
        self._bullet_list(priorities, x=right_x, width=col_w)
        right_end = self.y

        self.y = min(left_end, right_end)
        self._gap(14)
        self._hline(BORDER_LIGHT, 0.5)
        self._gap(14)

    def draw_dimension_table(self):
        self._text("Dimension Assessment",
                    font="Helvetica-Bold", size=11, color=NAVY)
        self._gap(10)

        # Column X positions
        c_dim = ML
        c_fit = ML + 152
        c_fit_bar = ML + 188
        c_risk = ML + 250
        c_risk_bar = ML + 288
        c_badge = ML + 350
        c_weight = PAGE_W - MR

        bar_w = 54
        bar_h = 5

        # Header row
        hy = self.y - 7
        self._text_at("DIMENSION", c_dim, hy,
                       font="Helvetica-Bold", size=6.5, color=TEXT_MUTED)
        self._text_at("FIT", c_fit, hy,
                       font="Helvetica-Bold", size=6.5, color=TEXT_MUTED)
        self._text_at("RISK", c_risk, hy,
                       font="Helvetica-Bold", size=6.5, color=TEXT_MUTED)
        self._text_at("LEVEL", c_badge, hy,
                       font="Helvetica-Bold", size=6.5, color=TEXT_MUTED)
        self._text_right_at("WEIGHT", c_weight, hy,
                             font="Helvetica-Bold", size=6.5, color=TEXT_MUTED)

        self.y -= 7
        self._gap(6)
        self._hline(BORDER, 0.5)
        self._gap(4)

        weights = self.d["weights"]

        for dim_key in DIM_ORDER:
            name, subtitle = DIMENSION_META[dim_key]
            fit = float(self.d["fits"].get(dim_key, 0))
            risk = float(self.d["risks"].get(dim_key, 0))
            rlevel = risk_bucket(risk)
            w_pct = float(weights.get(dim_key, 0)) * 100

            ry = self.y

            # Name + subtitle
            self._text_at(name, c_dim, ry - 10,
                           font="Helvetica-Bold", size=8.5, color=TEXT_PRIMARY)
            self._text_at(subtitle, c_dim, ry - 20,
                           font="Helvetica", size=6.5, color=TEXT_MUTED)

            # Fit score + bar
            self._text_at(f"{fit:.0f}%", c_fit, ry - 12,
                           font="Helvetica-Bold", size=8, color=TEXT_BODY)
            self._mini_bar(c_fit_bar, ry - 15, bar_w, bar_h, fit, BAR_FIT)

            # Risk score + bar
            self._text_at(f"{risk:.0f}%", c_risk, ry - 12,
                           font="Helvetica-Bold", size=8, color=TEXT_BODY)
            self._mini_bar(c_risk_bar, ry - 15, bar_w, bar_h, risk,
                           bar_color_for_risk(rlevel))

            # Risk level badge
            bg, fg = badge_colors(rlevel)
            self._badge(c_badge, ry - 18, rlevel.upper(), bg, fg, font_size=6)

            # Weight
            self._text_right_at(f"{w_pct:.0f}%", c_weight, ry - 12,
                                 font="Helvetica", size=7.5,
                                 color=TEXT_SECONDARY)

            self.y -= 28
            self._hline(BORDER_LIGHT, 0.3)
            self._gap(2)

        self._gap(12)

    def draw_evidence(self):
        self._text("Evidence Summary",
                    font="Helvetica-Bold", size=11, color=NAVY)
        self._gap(8)

        ext = self.d["extraction"]
        ev = self.d.get("evidence_summary", {})

        # Skills Detected
        skills_text = (", ".join(ext.get("skills", []))
                       or "No strong skill signals detected")
        self._para(
            f"<b>Skills Detected:</b>&nbsp;&nbsp;{_esc(skills_text)}",
            S_EVIDENCE)
        self._gap(3)

        # Coursework Signals
        courses_text = (", ".join(ext.get("courses", []))
                        or "None identified")
        self._para(
            f"<b>Coursework Signals:</b>&nbsp;&nbsp;{_esc(courses_text)}",
            S_EVIDENCE)
        self._gap(3)

        # Applied Evidence (curated from evidence pipeline)
        summary_lines = ev.get("summary_lines", [])
        if summary_lines:
            evidence_text = ". ".join(summary_lines)
            if not evidence_text.endswith("."):
                evidence_text += "."
        else:
            # Fallback to legacy projects if no evidence pipeline ran
            legacy = ext.get("projects", [])
            if legacy:
                evidence_text = "; ".join(legacy[:3])
            else:
                evidence_text = "None detected via profile scan"

        self._para(
            f"<b>Applied Evidence:</b>&nbsp;&nbsp;{_esc(evidence_text)}",
            S_EVIDENCE)
        self._gap(3)

        # Confidence indicator (if evidence pipeline ran)
        conf = ev.get("confidence")
        source_count = ev.get("source_count", 0)
        sections = ev.get("sections_covered", [])
        if conf and source_count > 0:
            section_str = ", ".join(s.title() for s in sections)
            meta = (f"{source_count} evidence items from "
                    f"{section_str or 'profile'} "
                    f"\u2014 confidence: {conf}")
            self._para(
                f"<b>Evidence Basis:</b>&nbsp;&nbsp;{_esc(meta)}",
                S_EVIDENCE)
            self._gap(3)

        # Work-Style Source
        self._para(
            "<b>Work-Style Source:</b>&nbsp;&nbsp;"
            "8-question behavioral questionnaire (Likert 1\u20135 scale)",
            S_EVIDENCE)
        self._gap(3)

        # Caution note (if present)
        caution = ev.get("caution_note")
        if caution:
            self._gap(2)
            self._para(
                f"<i>Note: {_esc(caution)}</i>",
                S_BODY_XS)

        self._gap(6)
        # Privacy note
        self._para(
            "Source documents are deleted immediately after scoring. "
            "Structured results are retained for a limited period only.",
            S_BODY_XS,
        )

    def draw_page1_footer(self):
        self._gap(8)
        self._hline(BORDER_LIGHT, 0.3)
        self._gap(4)
        self._text_at("SOCLAW Risk Assessment \u2014 Page 1 of 2", ML,
                        MB + 6, font="Helvetica", size=6, color=TEXT_MUTED)
        self._text_right_at("Generated by SOCLAW scoring engine",
                             PAGE_W - MR, MB + 6,
                             font="Helvetica", size=6, color=TEXT_MUTED)

    # ── page 2 components ─────────────────────────────────────

    def draw_page2(self):
        self.c.showPage()
        self.y = PAGE_H - MT
        self.draw_header()
        self.draw_interview_guide()
        self.draw_how_to_use()
        self.draw_methodology()
        self.draw_page2_footer()

    def draw_interview_guide(self):
        self._text("Targeted Interview Guide",
                    font="Helvetica-Bold", size=14, color=NAVY)
        self._gap(6)
        self._para(
            "The following questions are designed to validate the highest "
            "predicted risks and confirm observed strengths. Each question "
            "targets a specific assessment dimension.",
            S_BODY_SM,
        )
        self._gap(14)

        questions = self.d["interview_questions"]
        for i, q in enumerate(questions[:7], 1):
            dim, text = parse_interview_question(q)
            validates = VALIDATES_MAP.get(
                dim, VALIDATES_MAP.get(dim.split()[0] if dim else "General",
                                       "Overall candidate suitability"))

            # Numbered circle
            cx = ML + 8
            cy = self.y - 6
            self.c.setFillColor(TEAL)
            self.c.circle(cx, cy, 7.5, fill=1, stroke=0)
            self.c.setFont("Helvetica-Bold", 7)
            self.c.setFillColor(WHITE)
            self.c.drawCentredString(cx, cy - 2.5, str(i))

            # Dimension label
            self._text_at(dim.upper(), ML + 22, self.y - 4,
                           font="Helvetica-Bold", size=7.5, color=TEAL)
            self._gap(7)

            # Question text
            self._para(_esc(text), S_QUESTION, x=ML + 22, width=CW - 26)
            self._gap(2)

            # Validates line
            self._para(f"<i>Validates: {_esc(validates)}</i>",
                        S_VALIDATES, x=ML + 22, width=CW - 26)
            self._gap(10)

        self._gap(4)
        self._hline(BORDER_LIGHT, 0.5)
        self._gap(14)

    def draw_how_to_use(self):
        self._text("How to Use This Report",
                    font="Helvetica-Bold", size=10, color=NAVY)
        self._gap(6)

        points = [
            "This report is a structured decision-support tool designed to "
            "inform, not replace, human judgment.",
            "Elevated risk dimensions should be validated through targeted "
            "interview questions before extending an offer.",
            "Moderate risks are common and frequently resolve through "
            "structured behavioral questioning.",
            "Scores are relative to the target role and reflect predicted "
            "alignment, not absolute candidate quality.",
        ]
        self._bullet_list(points, style=S_BODY_SM)
        self._gap(10)
        self._hline(BORDER_LIGHT, 0.5)
        self._gap(12)

    def draw_methodology(self):
        self._text("Methodology & Trust",
                    font="Helvetica-Bold", size=10, color=NAVY)
        self._gap(10)

        blocks = [
            ("Deterministic Scoring",
             "All match scores, risk levels, and dimension weights are "
             "computed using deterministic, rules-based algorithms. Results "
             "are fully reproducible given identical inputs."),
            ("Explainable Dimensions",
             "Each SOCLAW dimension (Skill, Ownership, Context, Location, "
             "Adaptability, Work Style) is independently scored and weighted "
             "based on transparent, documented criteria."),
            ("AI Disclosure",
             "AI may assist with language refinement and interview question "
             "tailoring. AI does not calculate match scores, risk levels, "
             "or hard-stop logic. All quantitative scoring is deterministic."),
            ("Data Privacy",
             "Source documents (CVs, LinkedIn PDFs) are deleted immediately "
             "after extraction. Only structured assessment data is retained, "
             "subject to configurable retention policies."),
        ]

        col_w = (CW - 20) / 2
        positions = [
            (ML, col_w),
            (ML + col_w + 20, col_w),
        ]

        for row_idx in range(2):
            save_y = self.y
            max_consumed = 0
            for col_idx in range(2):
                idx = row_idx * 2 + col_idx
                title, body = blocks[idx]
                px, pw = positions[col_idx]
                self.y = save_y

                self.c.setFont("Helvetica-Bold", 7.5)
                self.c.setFillColor(NAVY)
                self.y -= 8
                self.c.drawString(px, self.y, title)
                self._gap(4)

                self._para(_esc(body), S_METH, x=px, width=pw)

                consumed = save_y - self.y
                if consumed > max_consumed:
                    max_consumed = consumed

            self.y = save_y - max_consumed
            self._gap(8)

    def draw_page2_footer(self):
        self._gap(8)
        self._hline(BORDER_LIGHT, 0.3)
        self._gap(4)
        self._text_at("SOCLAW Risk Assessment \u2014 Page 2 of 2", ML,
                        MB + 6, font="Helvetica", size=6, color=TEXT_MUTED)
        self._text_right_at("Generated by SOCLAW scoring engine",
                             PAGE_W - MR, MB + 6,
                             font="Helvetica", size=6, color=TEXT_MUTED)


# ── Public API ────────────────────────────────────────────────

def generate_pdf(
    out_path: str,
    candidate_name: str | None = None,
    candidate_email: str = "",
    job: dict = None,
    linkedin_url: str | None = None,
    extraction: dict = None,
    questionnaire: dict = None,
    weights: dict = None,
    fits: dict = None,
    risks: dict = None,
    match_score: int = 0,
    recommendation: str = "",
    flags: list[str] | None = None,
    interview_questions: list[str] | None = None,
    evidence_summary: dict | None = None,
    scoring_version: str = "v1.0",
) -> None:
    now = datetime.now(timezone.utc)
    report_date = f"{now.day} {now.strftime('%B %Y')}"

    display_name = (candidate_name or "").strip() or "Candidate not named"

    data = {
        "candidate_name": display_name,
        "candidate_email": candidate_email,
        "job": job or {},
        "linkedin_url": linkedin_url,
        "extraction": extraction or {},
        "questionnaire": questionnaire or {},
        "weights": weights or {},
        "fits": fits or {},
        "risks": risks or {},
        "match_score": match_score,
        "recommendation": recommendation,
        "flags": flags or [],
        "interview_questions": interview_questions or [],
        "evidence_summary": evidence_summary or {},
        "scoring_version": scoring_version,
        "report_date": report_date,
    }

    c = canvas_mod.Canvas(out_path, pagesize=A4)
    c.setTitle("SOCLAW \u2014 Corporate Internship Risk Assessment")
    c.setAuthor("SOCLAW Scoring Engine")

    r = _Renderer(c, data)

    # Page 1
    r.draw_header()
    r.draw_title_block()
    r.draw_executive_panel()
    r.draw_takeaways()
    r.draw_dimension_table()
    r.draw_evidence()
    r.draw_page1_footer()

    # Page 2
    r.draw_page2()

    c.save()
