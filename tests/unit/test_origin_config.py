from masyg_extractor.config import origins


def test_development_origins_include_localhost_alias(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "development")
    monkeypatch.setenv("CLIENT_URL", "http://localhost:4000/")
    monkeypatch.setenv("DEV_CLIENT_URL", "http://localhost:4000")
    monkeypatch.delenv("CORS_EXTRA", raising=False)

    assert origins.build_allowed_origins() == [
        "http://localhost:4000",
        "http://127.0.0.1:4000",
    ]


def test_extra_origins_are_normalized_and_deduplicated(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "development")
    monkeypatch.setenv("CLIENT_URL", "http://localhost:4000")
    monkeypatch.setenv("DEV_CLIENT_URL", "http://localhost:4000/")
    monkeypatch.setenv("CORS_EXTRA", "https://preview.example.com/, http://127.0.0.1:4000")

    assert origins.build_allowed_origins() == [
        "http://localhost:4000",
        "http://127.0.0.1:4000",
        "https://preview.example.com",
    ]


def test_production_does_not_invent_loopback_aliases(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "production")
    monkeypatch.setenv("CLIENT_URL", "https://app.masyglink.com")
    monkeypatch.setenv("DEV_CLIENT_URL", "http://localhost:4000")
    monkeypatch.delenv("CORS_EXTRA", raising=False)

    assert origins.build_allowed_origins() == [
        "https://app.masyglink.com",
    ]


def test_invalid_origins_are_ignored(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "development")
    monkeypatch.setenv("CLIENT_URL", "localhost:4000")
    monkeypatch.setenv("DEV_CLIENT_URL", "")
    monkeypatch.setenv("CORS_EXTRA", "javascript:alert(1), https://good.example.com/path")

    assert origins.build_allowed_origins() == []
