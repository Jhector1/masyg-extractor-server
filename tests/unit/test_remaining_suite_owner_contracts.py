from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_firestore_record_test_targets_current_quickbooks_repository():
    source = _source("tests/integration/firestore_record_test.py")

    assert (
        "masyg_extractor.integrations.quickbooks.repository."
        "firestore_repository"
    ) in source
    assert (
        "masyg_extractor.integrations.repository.firestore_repository"
    ) not in source


def test_obsolete_multiple_invoice_service_test_is_retired():
    assert not (
        ROOT / "tests/integration/multiple_invoice_test.py"
    ).exists()


def test_extractor_tools_test_matches_current_async_contract():
    source = _source("tests/test_extractor_tools.py")

    assert 'assert json_content == {"key": "value"}' in source
    assert "openai.ChatCompletion.create" not in source
    assert "asyncio.run(" in source
    assert "progress_logger=object()" in source


def test_user_route_tests_execute_without_external_async_pytest_plugin():
    source = _source("tests/test_user_routes/test_user.py")

    assert "SyncASGIClient" in source
    assert "@pytest.mark.asyncio" not in source
    assert "async def test_" not in source
    assert "await client." not in source
    assert "ASGITransport" not in source
    assert "AsyncClient" not in source

def test_user_route_tests_mock_external_runtime_dependencies():
    source = _source("tests/test_user_routes/test_user.py")

    assert 'monkeypatch.setattr(user_routes, "ref", users)' in source
    assert 'monkeypatch.setattr(user_routes, "users_coll", users)' in source
    assert '"add_new_user_async"' in source
    assert '"query_user_by_email_async"' in source
    assert '"create_refresh_session"' in source
    assert '"send_message_safely"' in source
    assert "firebase" not in source.lower()
