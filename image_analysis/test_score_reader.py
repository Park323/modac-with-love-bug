from image_analysis.score_reader import normalize_score, normalize_team


def test_normalize_team() -> None:
    assert normalize_team("GR") == "GR"
    assert normalize_team("6R") == "GR"
    assert normalize_team("BL") == "BL"
    assert normalize_team("8L") == "BL"


def test_normalize_score() -> None:
    assert normalize_score("0") == 0
    assert normalize_score("O") == 0
    assert normalize_score("12") == 12
