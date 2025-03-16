import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from unittest.mock import MagicMock, patch
from datetime import datetime

# Initialize Firebase (if needed)
from masyg_extractor.firebase.firebase_init import firebase_init
firebase_init()

# Import your router and the dependency to override.
from masyg_extractor.routes.data_extractor_routes import router
from masyg_extractor.services.dependencies import get_firebase_user

@pytest.fixture
def app():
    app = FastAPI()
    # Add SessionMiddleware so request.session is available.
    app.add_middleware(SessionMiddleware, secret_key="testkey")
    app.include_router(router)
    # Override the dependency so that we bypass accessing request.session.
    app.dependency_overrides[get_firebase_user] = lambda: {"userId": "user123"}
    return app

@pytest.fixture
def client(app):
    return TestClient(app)

# You can remove the auth_headers helper if you don't need to simulate session cookies.
# However, if your endpoint also checks for a header, you can still include it.
def auth_headers():
    user = {"userId": "user123"}
    return {"X-User": json.dumps(user)}

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_invalid_payload(mock_client, client):
    # Sending a payload with no "change_log" key should return an error.
    response = client.post(
        '/extractor/update-change-log',
        json={},
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 400
    assert 'error' in data

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_edit_action_with_field(mock_client, client):
    # For an EDIT record with a field, we expect the doc_ref.update to be called.
    mock_doc_ref = MagicMock()
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_client.return_value.document.return_value = mock_doc_ref

    payload = {
        "change_log": [
            {
                "action": "EDIT",
                "path": "groups/group1/files/file1.pdf",
                "field": "vendor_name",
                "oldValue": "Vendor A",
                "newValue": "Vendor Updated",
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
        ]
    }
    response = client.post(
        '/extractor/update-change-log',
        json=payload,
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 200
    assert data['message'] == 'Change log processed successfully.'
    mock_doc_ref.update.assert_called_with({'vendor_name': 'Vendor Updated'})

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_edit_action_without_field(mock_client, client):
    # For an EDIT record without a field, expect doc_ref.set(newValue, merge=True) to be called.
    mock_doc_ref = MagicMock()
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_client.return_value.document.return_value = mock_doc_ref

    payload = {
        "change_log": [
            {
                "action": "EDIT",
                "path": "groups/group1/files/file1.pdf",
                "newValue": {"line_items": [{"item_name": "Item 1"}]},
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
        ]
    }
    response = client.post(
        '/extractor/update-change-log',
        json=payload,
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 200
    mock_doc_ref.set.assert_called_with({"line_items": [{"item_name": "Item 1"}]}, merge=True)

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.DELETE_FIELD', 'DELETE_FIELD')
@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_delete_action_with_field(mock_client, client):
    # For a DELETE action with a field, expect update({field: 'DELETE_FIELD'}) to be called.
    mock_doc_ref = MagicMock()
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_client.return_value.document.return_value = mock_doc_ref

    payload = {
        "change_log": [
            {
                "action": "DELETE",
                "path": "groups/group1/files/file1.pdf",
                "field": "vendor_name",
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
        ]
    }
    response = client.post(
        '/extractor/update-change-log',
        json=payload,
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 200
    mock_doc_ref.update.assert_called_with({'vendor_name': 'DELETE_FIELD'})

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_add_action(mock_client, client):
    # For an ADD action, expect doc_ref.set(newValue, merge=True) to be called.
    mock_doc_ref = MagicMock()
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_client.return_value.document.return_value = mock_doc_ref

    payload = {
        "change_log": [
            {
                "action": "ADD",
                "path": "groups/group1/files/file1.pdf",
                "newValue": {"line_items": [{"item_name": "New Item"}]},
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
        ]
    }
    response = client.post(
        '/extractor/update-change-log',
        json=payload,
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 200
    mock_doc_ref.set.assert_called_with({"line_items": [{"item_name": "New Item"}]}, merge=True)

@patch('masyg_extractor.routes.data_extractor_routes.admin_fs.client')
def test_group_delete_action(mock_client, client):
    # For GROUP-DELETE, expect doc_ref.delete() to be called.
    mock_doc_ref = MagicMock()
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_client.return_value.document.return_value = mock_doc_ref

    payload = {
        "change_log": [
            {
                "action": "GROUP-DELETE",
                "path": "groups/group1",
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
        ]
    }
    response = client.post(
        '/extractor/update-change-log',
        json=payload,
        headers=auth_headers()
    )
    data = response.json()
    assert response.status_code == 200
    mock_doc_ref.delete.assert_called()
