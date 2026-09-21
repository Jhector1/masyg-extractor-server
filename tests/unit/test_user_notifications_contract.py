from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_notifications_are_durable_and_live():
    service = read("masyg_extractor/services/user_notifications.py")

    assert 'NOTIFICATIONS_COLLECTION = "notifications"' in service
    assert 'USER_NOTIFICATION_EVENT = "user-notification"' in service
    assert 'room=f"user:{kwargs[\'user_id\']}"' in service
    assert '"dedupeKey"' in service
    assert '"readAt": None' in service


def test_notification_api_is_authenticated_and_registered():
    routes = read("masyg_extractor/routes/notification_routes.py")
    registry = read("masyg_extractor/routes/__init__.py")

    assert "Depends(get_current_user_from_cookie)" in routes
    assert '@router.get("")' in routes
    assert '@router.patch("/{notification_id}/read")' in routes
    assert '@router.post("/read-all")' in routes
    assert 'app.include_router(notification_router, prefix="/api")' in registry
