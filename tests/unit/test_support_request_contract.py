from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_support_route_is_authenticated_and_reuses_mail_owner():
    route = source(
        "masyg_extractor/routes/user_routes.py"
    )

    assert '@router.post("/support")' in route
    assert (
        "Depends(\n"
        "        get_current_user_from_cookie"
        in route
    )
    assert "send_message_safely(" in route
    assert (
        'getattr(\n'
        '        request.app.state,\n'
        '        "mail"'
        in route
    )


def test_support_route_has_bounded_payload():
    route = source(
        "masyg_extractor/routes/user_routes.py"
    )

    assert "class SupportRequest(BaseModel)" in route
    assert "max_length=4000" in route
    assert "max_length=2048" in route
    assert "SUPPORT_TOPICS = {" in route


def test_support_route_uses_configured_support_recipient():
    route = source(
        "masyg_extractor/routes/user_routes.py"
    )

    assert 'os.getenv("SUPPORT_EMAIL")' in route
    assert "support@masyglink.com" in route
    assert "recipients=[SUPPORT_EMAIL]" in route


def test_support_route_does_not_create_second_mail_transport():
    route = source(
        "masyg_extractor/routes/user_routes.py"
    )

    assert "FastMail(" not in route
    assert "ConnectionConfig(" not in route
    assert "BREVO_PASSWORD" not in route
    assert "BREVO_USERNAME" not in route
