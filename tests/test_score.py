from src.score import score_answer


def test_score_improves_with_detail() -> None:
    short = score_answer("Yes.", "What is a list?")
    long = score_answer(
        "A list is a mutable sequence type in Python. For example, you can append items.",
        "What is a list?",
    )
    assert long.correctness > short.correctness
    assert long.uses_examples is True
