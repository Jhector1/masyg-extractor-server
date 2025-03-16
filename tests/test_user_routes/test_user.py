import pytest
import asyncio
import uuid
from httpx import AsyncClient
from server import asgi_app  # Import your ASGI-compatible fastapi app
import uuid
unique_email = f"test-{uuid.uuid4().hex}@example.com"

# Define an event loop fixture.
@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

# Use a synchronous fixture to instantiate the AsyncClient.
@pytest.fixture
def client(event_loop):
    ac = event_loop.run_until_complete(
        AsyncClient(app=asgi_app, base_url="http://test").__aenter__()
    )
    yield ac
    event_loop.run_until_complete(ac.__aexit__(None, None, None))

# ----- Test Cases -----

@pytest.mark.asyncio
async def test_user_signup_success(client):
    """Test successful user registration using a unique email."""
    unique_email = f"test-{uuid.uuid4().hex}@example.com"
    response = await client.post("/api/user/signup", json={
        "username": "testuser",
        "email": unique_email,
        "password": "SecurePass123!"
    })
    # For a new unique user, we expect a 201 status code.
    assert response.status_code == 201, f"Response: {response.text}"
    assert "userId" in response.json()

@pytest.mark.asyncio
async def test_user_signup_duplicate_email(client):
    """Test duplicate email registration."""
    # First signup using a specific email.
    email = "duplicate@example.com"
    await client.post("/api/user/signup", json={
        "username": "testuser",
        "email": email,
        "password": "SecurePass123!"
    })

    # Second signup with the same email should fail.
    response = await client.post("/api/user/signup", json={
        "username": "anotheruser",
        "email": email,
        "password": "SecurePass123!"
    })
    assert response.status_code == 400
    assert response.json()["message"] == "Email already exists"

@pytest.mark.asyncio
async def test_user_signup_invalid_email(client):
    """Test signup with an invalid email format."""
    response = await client.post("/api/user/signup", json={
        "username": "testuser",
        "email": "invalid-email",
        "password": "SecurePass123!"
    })
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_user_signup_missing_fields(client):
    """Test signup with missing required fields."""
    response = await client.post("/api/user/signup", json={})
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_user_login_success(client):
    """Test successful login."""
    unique_email = f"login-{uuid.uuid4().hex}@example.com"
    password = "SecurePass123!"

    # Create user.
    await client.post("/api/user/signup", json={
        "username": "loginuser",
        "email": unique_email,
        "password": password
    })

    # Attempt login.
    response = await client.post("/api/user/login", json={
        "email": unique_email,
        "password": password
    })
    assert response.status_code == 200
    assert "user" in response.json()

@pytest.mark.asyncio
async def test_user_login_wrong_password(client):
    """Test login with wrong password."""
    email = "wrongpass@example.com"
    await client.post("/api/user/signup", json={
        "username": "wrongpass",
        "email": email,
        "password": "CorrectPass123!"
    })

    response = await client.post("/api/user/login", json={
        "email": email,
        "password": "WrongPass!"
    })
    assert response.status_code == 400
    assert response.json()["message"] == "Invalid email or password"

@pytest.mark.asyncio
async def test_user_login_nonexistent_user(client):
    """Test login with a non-existent user."""
    response = await client.post("/api/user/login", json={
        "email": "doesnotexist@example.com",
        "password": "RandomPass!"
    })
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_user_login_missing_fields(client):
    """Test login with missing fields."""
    response = await client.post("/api/user/login", json={"email": ""})
    assert response.status_code == 400
