import json
import time
import logging

from groq import Groq

from .settings import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a recruitment assessment expert. Given a job description and a \
candidate's extracted profile data, generate exactly 3 tailored interview \
questions that probe the candidate's fit for this specific role.

Rules:
- Each question must be specific to the gap between the candidate and the job.
- Questions should be behavioral ("Tell me about a time…") or situational.
- Return ONLY a JSON array of 3 strings, no markdown, no explanation.
Example: ["Question 1?", "Question 2?", "Question 3?"]
"""


class AIClient:
    """
    Groq-powered AI client for generating tailored interview questions.
    Falls back gracefully to None (deterministic questions) on any failure.
    """
    def __init__(self):
        self.enabled = settings.ai_enabled and bool(settings.ai_api_key)
        if self.enabled:
            self.client = Groq(api_key=settings.ai_api_key)
            self.model = settings.ai_model

    def generate_tailored_questions(self, payload: dict) -> list[str] | None:
        if not self.enabled:
            return None

        user_msg = json.dumps(payload, default=str)

        for attempt in [1, 2]:
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.3,
                    max_tokens=512,
                    timeout=settings.ai_timeout_seconds,
                )
                text = resp.choices[0].message.content.strip()
                questions = json.loads(text)
                if isinstance(questions, list) and len(questions) >= 3:
                    return [str(q) for q in questions[:5]]
                log.warning("AI returned invalid format: %s", text)
                return None
            except Exception as e:
                log.warning("AI attempt %d failed: %s", attempt, e)
                if attempt == 2:
                    return None
                time.sleep(0.5)
        return None
