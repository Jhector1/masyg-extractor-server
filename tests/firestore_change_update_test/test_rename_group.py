import pytest
from test_support.sync_asgi_client import SyncASGIClient

from server import app  # your FastAPI app
from masyg_extractor.services.firestore_helpers import get_firestore_client
from masyg_extractor.config.jwt_config import get_current_user_from_cookie
import masyg_extractor.routes.data_extractor_routes as data_extractor_routes

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

async def fake_document_update(
    doc_ref: FakeDocumentReference,
    update_data: dict,
):
    doc = doc_ref.parent_data.get(doc_ref.id)
    if doc is None:
        return None

    for field_path, value in update_data.items():
        target = doc
        parts = field_path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    return None

async def fake_get_current_user_from_cookie():
    return {"userId": "test_user"}

# Apply dependency overrides.
app.dependency_overrides[get_current_user_from_cookie] = fake_get_current_user_from_cookie

data_extractor_routes.get_firestore_client = fake_get_firestore_client
data_extractor_routes.document_get = fake_document_get
data_extractor_routes.document_update = fake_document_update

client = SyncASGIClient(app)

# ----- Tests for update-group-name (already present) -----

def test_update_group_name_success():
    group_id = "group1"
    new_name = "NewGroupName"
    response = client.put(
        f"/api/extractor/update-group-name/{group_id}",
        json={"group_name": new_name},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "group_name": new_name,
        "message": f"Group name updated to {new_name} for {group_id}.",
    }

    updated_name = (
        fake_db["users"]["test_user"]["groups"][group_id]["metadata"]["group_name"]
    )
    assert updated_name == new_name


def test_update_group_name_not_found():
    group_id = "nonexistent"
    new_name = "AnyName"
    response = client.put(
        f"/api/extractor/update-group-name/{group_id}",
        json={"group_name": new_name},
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == (
        f"No group found with group_id: {group_id}"
    )

def test_update_group_name_missing_payload():
    group_id = "group1"
    response = client.put(
        f"/api/extractor/update-group-name/{group_id}",
        json={},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid request payload"
