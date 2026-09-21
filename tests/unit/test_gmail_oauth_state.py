from cryptography.fernet import Fernet
import pytest

from masyg_extractor.integrations.document_sources.gmail.oauth_state import (
    GmailStateError,
    issue_gmail_state,
    read_gmail_state,
)


def test_gmail_oauth_state_round_trip(
    monkeypatch,
):
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    state = issue_gmail_state(
        "user-123"
    )

    assert state
    assert "user-123" not in state
    assert read_gmail_state(
        state
    ) == "user-123"


def test_gmail_oauth_state_rejects_tampering(
    monkeypatch,
):
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    state = issue_gmail_state(
        "user-123"
    )

    # Corrupt a byte-bearing character in the middle of the Fernet token.
    # Changing only the final base64 character is not a reliable tamper test
    # because unused padding bits can decode to the same underlying bytes.
    midpoint = len(state) // 2
    replacement = "A" if state[midpoint] != "A" else "B"
    tampered = (
        state[:midpoint]
        + replacement
        + state[midpoint + 1:]
    )

    assert tampered != state

    with pytest.raises(
        GmailStateError
    ):
        read_gmail_state(
            tampered
        )
