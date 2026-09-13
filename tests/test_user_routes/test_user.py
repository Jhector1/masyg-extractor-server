import pytest

from server import app
from test_support.sync_asgi_client import SyncASGIClient
import masyg_extractor.routes.user_routes as user_routes


class FakeSnapshot:
    def __init__(self, data=None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeTrialDocument:
    def get(self):
        return FakeSnapshot()


class FakePlanCollection:
    def document(self, _doc_id):
        return FakeTrialDocument()


class FakeUserDocument:
    def __init__(self, user_store, user_id):
        self._user_store = user_store
        self._user_id = user_id

    def _find(self):
        for email, user in self._user_store.items():
            if user.get("userId") == self._user_id:
                return email, user
        return None, None

    def update(self, updates):
        _email, user = self._find()
        if user is not None:
            user.update(updates)

    def collection(self, name):
        assert name == "plan"
        return FakePlanCollection()


class FakeEmailQuery:
    def __init__(self, user_store, email=None):
        self._user_store = user_store
        self._email = email

    def limit(self, _count):
        return self

    def stream(self):
        user = self._user_store.get(self._email)
        return [FakeSnapshot(user)] if user else []


class FakeUsersCollection:
    def __init__(self, user_store):
        self._user_store = user_store

    def where(self, *args, **kwargs):
        filter_obj = kwargs.get("filter")
        email = getattr(filter_obj, "value", None)
        return FakeEmailQuery(self._user_store, email)

    def document(self, user_id=None):
        assert user_id is not None
        return FakeUserDocument(self._user_store, user_id)


@pytest.fixture
def user_store():
    return {}


@pytest.fixture
def client(monkeypatch, user_store):
    users = FakeUsersCollection(user_store)

    async def fake_add_new_user_async(new_user, timeout_secs=10):
        del timeout_secs
        user_id = f"user-{len(user_store) + 1}"
        stored = dict(new_user)
        stored["userId"] = user_id
        user_store[stored["email"]] = stored
        return stored

    async def fake_query_user_by_email_async(email, timeout_secs=10):
        del timeout_secs
        user = user_store.get(email)
        return dict(user) if user else None

    async def fake_update_last_login_async(_user_id, timeout_secs=10):
        del timeout_secs

    async def fake_create_refresh_session(*_args, **_kwargs):
        return None

    async def fake_send_message_safely(*_args, **_kwargs):
        return None

    monkeypatch.setattr(user_routes, "ref", users)
    monkeypatch.setattr(user_routes, "users_coll", users)
    monkeypatch.setattr(
        user_routes,
        "add_new_user_async",
        fake_add_new_user_async,
    )
    monkeypatch.setattr(
        user_routes,
        "query_user_by_email_async",
        fake_query_user_by_email_async,
    )
    monkeypatch.setattr(
        user_routes,
        "update_last_login_async",
        fake_update_last_login_async,
    )
    monkeypatch.setattr(
        user_routes,
        "create_refresh_session",
        fake_create_refresh_session,
    )
    monkeypatch.setattr(
        user_routes,
        "send_message_safely",
        fake_send_message_safely,
    )
    monkeypatch.setattr(
        user_routes,
        "create_access_token",
        lambda **_kwargs: "test-access-token",
    )
    monkeypatch.setattr(
        user_routes,
        "create_refresh_token",
        lambda **_kwargs: "test-refresh-token",
    )
    monkeypatch.setattr(
        user_routes,
        "generate_csrf_token",
        lambda: "test-csrf-token",
    )

    # signup reads request.app.state.mail before queueing the background task.
    monkeypatch.setattr(app.state, "mail", object(), raising=False)

    return SyncASGIClient(app)


def test_user_signup_success(client):
    response = client.post(
        "/api/user/signup",
        json={
            "username": "testuser",
            "email": "new@example.com",
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["message"] == "User created"
    assert response.json()["userId"] == "user-1"


def test_user_signup_duplicate_email(client):
    email = "duplicate@example.com"

    first = client.post(
        "/api/user/signup",
        json={
            "username": "testuser",
            "email": email,
            "password": "SecurePass123!",
        },
    )
    assert first.status_code == 201, first.text

    response = client.post(
        "/api/user/signup",
        json={
            "username": "anotheruser",
            "email": email,
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Email already exists"


def test_user_signup_normalizes_email(client, user_store):
    response = client.post(
        "/api/user/signup",
        json={
            "username": "normalized",
            "email": "  Normalized@Example.COM  ",
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 201, response.text
    assert "normalized@example.com" in user_store


def test_user_signup_missing_fields(client):
    response = client.post("/api/user/signup", json={})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "No data provided"


def test_user_login_success(client):
    email = "login@example.com"
    password = "SecurePass123!"

    signup = client.post(
        "/api/user/signup",
        json={
            "username": "loginuser",
            "email": email,
            "password": password,
        },
    )
    assert signup.status_code == 201, signup.text

    response = client.post(
        "/api/user/login",
        json={
            "email": email,
            "password": password,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["message"] == "Login successful"
    assert data["user"]["email"] == email
    assert data["user"]["userId"] == "user-1"


def test_user_login_wrong_password(client):
    email = "wrongpass@example.com"

    signup = client.post(
        "/api/user/signup",
        json={
            "username": "wrongpass",
            "email": email,
            "password": "CorrectPass123!",
        },
    )
    assert signup.status_code == 201, signup.text

    response = client.post(
        "/api/user/login",
        json={
            "email": email,
            "password": "WrongPass!",
        },
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid email or password"


def test_user_login_nonexistent_user(client):
    response = client.post(
        "/api/user/login",
        json={
            "email": "doesnotexist@example.com",
            "password": "RandomPass!",
        },
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid email or password"


def test_user_login_missing_fields(client):
    response = client.post(
        "/api/user/login",
        json={"email": ""},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Email and password are required"
