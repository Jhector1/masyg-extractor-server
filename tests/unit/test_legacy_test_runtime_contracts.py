from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_sync_route_tests_use_project_owned_httpx_transport_adapter():
    for relative_path in (
        "tests/firestore_change_update_test/test_rename_group.py",
        "tests/image_extractor_test.py",
    ):
        source = _source(relative_path)
        assert (
            "from test_support.sync_asgi_client import SyncASGIClient"
            in source
        )
        assert "client = SyncASGIClient(app)" in source
        assert "from fastapi.testclient import TestClient" not in source
        assert "from starlette.testclient import TestClient" not in source
        assert "client = TestClient(app)" not in source


def test_sync_asgi_adapter_uses_modern_httpx_transport():
    helper = _source("test_support/sync_asgi_client.py")

    assert "httpx.ASGITransport" in helper
    assert "httpx.AsyncClient" in helper
    assert "asyncio.run(send())" in helper
    assert "self._cookies.update(response.cookies)" in helper


def test_image_route_override_has_no_untyped_request_parameter():
    image_test = _source("tests/image_extractor_test.py")

    assert "def override_get_current_user_from_cookie():" in image_test
    assert (
        "def override_get_current_user_from_cookie(request)"
        not in image_test
    )


def test_obsolete_rename_group_id_route_is_not_exercised():
    rename_test = _source(
        "tests/firestore_change_update_test/test_rename_group.py"
    )

    assert "/rename-group-id/" not in rename_test
    assert "/update-group-name/" in rename_test

def test_update_group_name_test_patches_route_owned_firestore_client():
    rename_test = _source(
        "tests/firestore_change_update_test/test_rename_group.py"
    )

    assert (
        "data_extractor_routes.get_firestore_client = "
        "fake_get_firestore_client"
    ) in rename_test
    assert (
        "app.dependency_overrides[get_firestore_client] = "
        "fake_get_firestore_client"
    ) not in rename_test
