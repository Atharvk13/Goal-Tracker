import pytest

from goal_tracker.progress.duplicates import (
    calculate_entry_hash,
    normalize_entry_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "Completed tutorial",
        "COMPLETED TUTORIAL",
        "  completed   tutorial  ",
        "completed\ttutorial",
        "completed\ntutorial",
        "Ｃｏｍｐｌｅｔｅｄ tutorial",
    ],
)
def test_equivalent_text_has_same_fingerprint(text):
    assert normalize_entry_text(text) == "completed tutorial"
    assert calculate_entry_hash(text) == calculate_entry_hash(
        "Completed tutorial"
    )


def test_equivalent_unicode_accents_match():
    composed = "Learned café terminology"
    decomposed = "Learned cafe\u0301 terminology"

    assert calculate_entry_hash(composed) == calculate_entry_hash(decomposed)


def test_different_content_has_different_fingerprint():
    assert calculate_entry_hash(
        "Completed tutorial"
    ) != calculate_entry_hash(
        "Pending tutorial"
    )


def test_punctuation_remains_significant():
    assert calculate_entry_hash(
        "Completed tutorial"
    ) != calculate_entry_hash(
        "Completed tutorial."
    )


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_is_rejected(text):
    with pytest.raises(ValueError, match="cannot be blank"):
        calculate_entry_hash(text)


def test_original_text_is_not_modified():
    original = "  COMPLETED   tutorial\n"

    normalize_entry_text(original)
    calculate_entry_hash(original)

    assert original == "  COMPLETED   tutorial\n"