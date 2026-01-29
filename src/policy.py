from typing import Dict, Tuple


class Policy:
    def __init__(self, config: Dict[str, object]) -> None:
        self._config = config

    def detect_role_reversal(self, text: str) -> bool:
        return "?" in text

    def role_reversal_reply(self) -> str:
        return self._config["role_reversal_reply"]

    def action_from_score(self, correctness: float, confidence: float) -> Tuple[str, str]:
        reasons = self._config["action_reasons"]
        if correctness > 0.8 and confidence > 0.7:
            return "increase", reasons["increase"]
        if correctness < 0.4:
            return "decrease", reasons["decrease"]
        return "same", reasons["same"]
