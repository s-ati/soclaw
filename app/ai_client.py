import time
from .settings import settings


class AIClient:
    """
    Stub interface.
    Later you can implement any free provider you find by only editing this file.
    The rest of the system remains stable.
    """
    def __init__(self):
        self.enabled = settings.ai_enabled and bool(settings.ai_api_key)

    def generate_tailored_questions(self, payload: dict) -> list[str] | None:
        if not self.enabled:
            return None

        # SAFE RETRY ONCE
        for attempt in [1, 2]:
            try:
                # TODO: implement provider call (Kimi or other)
                # Must return a list[str] length >= 3.
                # Keep temperature low and request JSON output.
                raise NotImplementedError("AI provider not configured")
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(0.4)
        return None
