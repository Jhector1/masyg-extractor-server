import pytest
from fastapi.testclient import TestClient

from server import app  # your FastAPI app
from masyg_extractor.services.firestore_helpers import get_firestore_client
from masyg_extractor.services.dependencies import get_firebase_user

# ----- Fake Firestore Implementation -----

# Global in-memory fake Firestore database.
fake_db = {}

@pytest.fixture(autouse=True)
def reset_fake_db():
    global fake_db
    fake_db.clear()
    fake_db.update({
        "users": {
            "test_user": {
                "groups": {
                    "group1": {"metadata": {"name": "Group1"}},
                    "group2": {"metadata": {"name": "Group2"}},
                }
            }
        }
    })

class FakeDocumentSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return self._data

class FakeDocumentReference:
    def __init__(self, doc_id: str, parent_data: dict):
        self.id = doc_id
        self.parent_data = parent_data  # Dictionary representing the collection

    def get(self):
        data = self.parent_data.get(self.id)
        return FakeDocumentSnapshot(self.id, data)

    def update(self, update_dict: dict):
        doc = self.parent_data.get(self.id, {})
        for key, value in update_dict.items():
            if "." in key:
                parts = key.split(".")
                sub = doc
                for part in parts[:-1]:
                    if part not in sub or not isinstance(sub[part], dict):
                        sub[part] = {}
                    sub = sub[part]
                sub[parts[-1]] = value
            else:
                doc[key] = value
        self.parent_data[self.id] = doc

    def set(self, data, merge=False):
        """Simulate Firestore's set() behavior."""
        if not merge:
            # Overwrite the entire doc
            self.parent_data[self.id] = data
        else:
            # Merge
            existing = self.parent_data.get(self.id, {})
            existing.update(data)
            self.parent_data[self.id] = existing

    def delete(self):
        if self.id in self.parent_data:
            del self.parent_data[self.id]

    def collection(self, collection_name: str):
        doc = self.parent_data.get(self.id)
        if doc is None:
            doc = {}
            self.parent_data[self.id] = doc
        if collection_name not in doc:
            doc[collection_name] = {}
        return FakeCollectionReference(doc[collection_name])

class FakeCollectionReference:
    def __init__(self, data: dict):
        self.data = data  # Dictionary mapping document IDs to document data

    def document(self, doc_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(doc_id, self.data)

    def stream(self):
        return [FakeDocumentSnapshot(doc_id, doc_data) for doc_id, doc_data in self.data.items()]

class FakeFirestoreClient:
    def __init__(self, data: dict):
        self.data = data

    def collection(self, collection_name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self.data.get(collection_name, {}))

# ----- Fake Dependency Overrides -----

async def fake_get_firestore_client():
    return FakeFirestoreClient(fake_db)

async def fake_document_get(doc_ref: FakeDocumentReference):
    return doc_ref.get()

async def fake_document_update(doc_ref: FakeDocumentReference, update_data: dict):
    return doc_ref.update(update_data)

async def fake_get_firebase_user():
    return {"userId": "test_user"}

# Apply dependency overrides.
app.dependency_overrides[get_firestore_client] = fake_get_firestore_client
app.dependency_overrides[get_firebase_user] = fake_get_firebase_user

import masyg_extractor.services.firestore_helpers as fs_helpers
fs_helpers.document_get = fake_document_get
fs_helpers.document_update = fake_document_update

client = TestClient(app)

# ----- Tests for update-group-name (already present) -----

def test_update_group_name_success():
    group_id = "group1"
    new_name = "NewGroupName"
    response = client.put(f"/api/extractor/rename-group-id/{group_id}", json={"name": new_name})
    assert response.status_code == 200, response.text
    json_resp = response.json()
    assert f"Group name updated successfully to '{new_name}'" in json_resp["message"]

    # Verify that fake_db is updated.
    updated_name = fake_db["users"]["test_user"]["groups"][group_id]["metadata"]["name"]
    assert updated_name == new_name

def test_update_group_name_duplicate():
    group_id = "group1"
    duplicate_name = "Group2"  # Already used in group2.
    response = client.put(f"/api/extractor/rename-group-id/{group_id}", json={"name": duplicate_name})
    assert response.status_code == 400, response.text
    json_resp = response.json()
    assert f"Group name '{duplicate_name}' already exists." in json_resp["detail"]

def test_update_group_name_not_found():
    group_id = "nonexistent"
    new_name = "AnyName"
    response = client.put(f"/api/extractor/rename-group-id/{group_id}", json={"name": new_name})
    assert response.status_code == 404, response.text
    json_resp = response.json()
    assert f"No group found with group_id: {group_id}" in json_resp["detail"]

def test_update_group_name_missing_payload():
    group_id = "group1"
    response = client.put(f"/api/extractor/rename-group-id/{group_id}", json={})
    assert response.status_code == 400, response.text
    json_resp = response.json()
    assert "Invalid request payload" in json_resp["detail"]

# ----- New Tests for rename-group-id endpoint -----

def test_rename_group_id_success():
    """
    Test renaming an existing group1 to 'groupX'. The old doc should be deleted
    and the new doc with the same data should be created.
    """
    old_group_id = "group1"
    new_group_id = "groupX"
    response = client.put(
        f"/api/extractor/rename-group-id/{old_group_id}",
        json={"new_group_id": new_group_id}
    )
    assert response.status_code == 200, response.text
    json_resp = response.json()
    assert f"Group ID renamed from {old_group_id} to {new_group_id}." in json_resp["message"]

    # Old doc should be removed.
    assert old_group_id not in fake_db["users"]["test_user"]["groups"]
    # New doc should exist with the same data.
    new_doc = fake_db["users"]["test_user"]["groups"].get(new_group_id)
    assert new_doc is not None
    # The 'metadata' content should match the old doc's data.
    assert new_doc["metadata"]["name"] == "Group1"

def test_rename_group_id_not_found():
    """
    Attempt to rename a group that doesn't exist should return 404.
    """
    old_group_id = "nonexistent"
    new_group_id = "groupX"
    response = client.put(
        f"/api/extractor/rename-group-id/{old_group_id}",
        json={"new_group_id": new_group_id}
    )
    assert response.status_code == 404, response.text
    json_resp = response.json()
    assert f"No group found with group_id: {old_group_id}" in json_resp["detail"]

def test_rename_group_id_duplicate():
    """
    Attempt to rename group1 to group2, which already exists. Should return 400.
    """
    old_group_id = "group1"
    new_group_id = "group2"
    response = client.put(
        f"/api/extractor/rename-group-id/{old_group_id}",
        json={"new_group_id": new_group_id}
    )
    assert response.status_code == 400, response.text
    json_resp = response.json()
    assert f"Group with group_id '{new_group_id}' already exists." in json_resp["detail"]

def test_rename_group_id_missing_payload():
    """
    Missing 'new_group_id' in the request payload should return 400.
    """
    old_group_id = "group1"
    response = client.put(
        f"/api/extractor/rename-group-id/{old_group_id}",
        json={}
    )
    assert response.status_code == 400, response.text
    json_resp = response.json()
    assert "Missing new_group_id" in json_resp["detail"]
