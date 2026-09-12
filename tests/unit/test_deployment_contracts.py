from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_context_excludes_secret_env_files():
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "**/.env" in dockerignore
    assert ".venv" in dockerignore


def test_compose_healthcheck_has_matching_endpoint():
    compose = (ROOT / "docker-compose.yml").read_text()
    server = (ROOT / "server.py").read_text()
    assert "http://127.0.0.1:5000/health" in compose
    assert '@inner.get("/health"' in server


def test_csrf_router_is_registered():
    routes = (ROOT / "masyg_extractor/routes/__init__.py").read_text()
    assert "app.include_router(csrf_router)" in routes


def test_debug_router_is_environment_gated():
    routes = (ROOT / "masyg_extractor/routes/__init__.py").read_text()
    assert '!= "production"' in routes
    assert "app.include_router(debug_router)" in routes


def test_dummy_extraction_route_is_environment_gated():
    routes = (ROOT / "masyg_extractor/routes/__init__.py").read_text()
    assert 'if environment != "production":' in routes
    assert "dummy_endpoint.fake_http" in routes


def test_direct_server_run_does_not_reimport_app_when_reload_is_off():
    server = (ROOT / "server.py").read_text()
    assert 'target = "server:app" if reload_enabled else app' in server
    assert "uvicorn.run(\n      target," in server


def test_admin_webhook_has_no_predictable_default_secret():
    webhook = (ROOT / "masyg_extractor/routes/admin/admin_webhook.py").read_text()
    assert 'os.getenv("WEBHOOK_SECRET")' in webhook
    assert 'os.getenv("WEBHOOK_SECRET", "your_webhook_secret")' not in webhook
    assert 'webhook_secret == "your_webhook_secret"' in webhook


def test_payment_mutations_use_cookie_auth_not_legacy_session_auth():
    payment = (ROOT / "masyg_extractor/routes/payment_routes.py").read_text()
    delete_start = payment.index('@router.post("/payment-method/delete")')
    reactivate_start = payment.index('@router.post("/subscription/reactivate")')
    delete_block = payment[delete_start:reactivate_start]
    reactivate_block = payment[reactivate_start:]

    assert "Depends(get_current_user_from_cookie)" in delete_block
    assert 'request.session.get("user")' not in delete_block
    assert "Depends(get_current_user_from_cookie)" in reactivate_block
    assert 'request.session.get("user")' not in reactivate_block
    assert "except HTTPException:" in delete_block
    assert "except HTTPException:" in reactivate_block


def test_registered_quickbooks_test_endpoint_is_hidden_in_production_and_errors_are_generic():
    qb = (ROOT / "masyg_extractor/integration_qb_v5/routers/qb_router.py").read_text()
    assert 'if os.getenv("FAST_API_ENV", "development").lower() == "production":' in qb
    assert 'raise HTTPException(status_code=404, detail="Not found")' in qb
    assert 'detail=str(e)' not in qb
    assert '"details": str(e)' not in qb


def test_quickbooks_accounts_call_is_offloaded_and_provider_details_are_not_reflected():
    qb = (ROOT / "masyg_extractor/integration_qb_v5/routers/qb_router.py").read_text()
    assert "await asyncio.to_thread(" in qb
    assert "requests.get," in qb
    assert 'detail="QuickBooks query failed"' in qb
    assert '"details": err' not in qb
    assert 'detail=f"Unexpected error:' not in qb


def test_user_profile_stripe_sync_is_offloaded():
    user_routes = (ROOT / "masyg_extractor/routes/user_routes.py").read_text()
    assert "await asyncio.to_thread(\n                stripe.Customer.modify" in user_routes
    assert 'detail="Failed to synchronize billing email"' in user_routes


def test_socketio_redis_probe_has_short_connect_timeouts():
    extensions = (ROOT / "masyg_extractor/utils/extensions.py").read_text()
    assert "socket_connect_timeout=1" in extensions
    assert "socket_timeout=1" in extensions


def test_socket_rooms_are_bound_to_signed_session_and_duplicates_are_replaced():
    server = (ROOT / "server.py").read_text()
    socket_owner = (ROOT / "masyg_extractor/services/socket_connections.py").read_text()
    assert "resolve_session_client_id(scope, auth)" in server
    assert "await socket_connections.claim(client_id, sid)" in server
    assert "await sio.disconnect(previous_sid)" in server
    assert 'session.get("client_id")' in socket_owner
    assert "requested != session_client_id" in socket_owner


def test_access_and_refresh_tokens_have_distinct_types_and_refresh_rotates():
    jwt_config = (ROOT / "masyg_extractor/config/jwt_config.py").read_text()
    user_routes = (ROOT / "masyg_extractor/routes/user_routes.py").read_text()
    assert 'token_type="access"' in jwt_config
    assert 'token_type="refresh"' in jwt_config
    assert 'expected_type="access"' in jwt_config
    assert 'expected_type="refresh"' in user_routes
    assert "rotate_refresh_session(" in user_routes
    assert "new_refresh_token = create_refresh_token(" in user_routes
    assert "revoke_refresh_session(" in user_routes


def test_legacy_duplicate_user_service_is_removed_and_session_is_not_auth_source():
    assert not (ROOT / "masyg_extractor/services/user_service.py").exists()
    payment = (ROOT / "masyg_extractor/routes/payment_routes.py").read_text()
    webhook = (ROOT / "masyg_extractor/routes/admin/admin_webhook.py").read_text()
    assert 'request.session["user"]' not in payment
    assert 'request.session.get("user")' not in webhook


def test_password_reset_revokes_refresh_sessions_and_logout_disconnects_socket():
    user_routes = (ROOT / "masyg_extractor/routes/user_routes.py").read_text()
    reset_start = user_routes.index('@router.post("/reset-password")')
    portal_start = user_routes.index('@router.post("/create-customer-portal")')
    reset_block = user_routes[reset_start:portal_start]
    logout_start = user_routes.index('@router.post("/logout")')
    update_start = user_routes.index('@router.post("/update")')
    logout_block = user_routes[logout_start:update_start]

    assert "await revoke_all_refresh_sessions(user_doc.id)" in reset_block
    assert "await revoke_refresh_session(user_id, auth_session_id)" in logout_block
    assert "await socket_connections.current_sid(client_id)" in logout_block
    assert "await sio.disconnect(sid)" in logout_block


def test_account_delete_cleans_refresh_sessions_before_parent_document():
    user_routes = (ROOT / "masyg_extractor/routes/user_routes.py").read_text()
    delete_start = user_routes.index('@router.delete("/delete-my-account/{email}")')
    login_start = user_routes.index('@router.post("/login")')
    delete_block = user_routes[delete_start:login_start]
    assert delete_block.index("await revoke_all_refresh_sessions(user_id)") < delete_block.index("await document_delete(user_ref)")


def test_successful_login_converges_legacy_auth_provider_metadata():
    user_routes = (ROOT / "masyg_extractor/routes/user_routes.py").read_text()
    login_start = user_routes.index('@router.post("/login")')
    login_block = user_routes[login_start:]
    assert 'login_provider = "google"' in login_block
    assert 'login_provider = "password"' in login_block
    assert '{"authProviders": firestore.ArrayUnion([login_provider])}' in login_block


def test_http_and_socketio_share_one_canonical_origin_allowlist():
    server = (ROOT / "server.py").read_text()
    extensions = (ROOT / "masyg_extractor/utils/extensions.py").read_text()
    origins = (ROOT / "masyg_extractor/config/origins.py").read_text()

    assert "from masyg_extractor.config.origins import ALLOWED_ORIGINS" in server
    assert "allow_origins=ALLOWED_ORIGINS" in server
    assert "from masyg_extractor.config.origins import ALLOWED_ORIGINS" in extensions
    assert "cors_allowed_origins=ALLOWED_ORIGINS" in extensions
    assert "DEV_CLIENT_URL" in origins
    assert "CORS_EXTRA" in origins


def test_auth_runtime_smoke_script_covers_rotation_replay_and_logout():
    smoke = (ROOT / "scripts/smoke_auth_runtime.py").read_text()
    assert '"/api/user/login"' in smoke
    assert "credentialed CORS preflight" in smoke
    assert "access-control-allow-origin" in smoke
    assert '"/api/user/current"' in smoke
    assert '"/api/user/refresh-token"' in smoke
    assert "old refresh replay rejected" in smoke
    assert '"/api/user/logout"' in smoke
    assert "refresh rejected after logout" in smoke
