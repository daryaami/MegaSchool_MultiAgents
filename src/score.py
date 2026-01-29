from dataclasses import dataclass


@dataclass
class Score:
    correctness: float
    confidence_estimate: float
    verbosity: float
    uses_examples: bool


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_answer(answer: str, question: str) -> Score:
    text = answer.lower().strip()
    length = len(text)
    verbosity = _clamp(length / 300.0)

    uses_examples = "for example" in text or "например" in text or "example" in text
    base = 0.2
    if length > 40:
        base += 0.2
    if length > 120:
        base += 0.2
    if uses_examples:
        base += 0.1
    if "don't know" in text or "не знаю" in text:
        base -= 0.3
    if question and any(word in text for word in question.lower().split()[:3]):
        base += 0.1

    correctness = _clamp(base)
    confidence_estimate = _clamp(0.4 + (0.4 if length > 80 else 0.1))
    return Score(
        correctness=correctness,
        confidence_estimate=confidence_estimate,
        verbosity=verbosity,
        uses_examples=uses_examples,
    )
