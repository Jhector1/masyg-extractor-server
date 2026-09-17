from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_accounting_token_repository_encrypts_before_firestore_write():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/token_repository.py"
    ).read_text()

    assert "seal_token_data(" in source
    assert "unseal_token_data(" in source
    assert '{"tokenData": migrated_storage}' not in source
    assert 'doc_ref.update(\n            {"tokenData": stored_token_data}' in source
    assert 'doc_ref.set({"tokenData": token_data}, merge=True)' not in source


def test_secret_envelope_uses_aes_gcm_and_owner_bound_aad():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/integration_secret_crypto.py"
    ).read_text()

    assert "AESGCM(" in source
    assert '_ALGORITHM = "AES-256-GCM"' in source
    assert '"userId": _normalized_user_id(user_id)' in source
    assert '"integration": _normalized_provider(integration)' in source
    assert '"keyVersion": key_version' in source


def test_accounting_runtime_contract_remains_plaintext_in_memory_only():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/shared/integration_secret_crypto.py"
    ).read_text()

    assert "runtime = {**metadata, **secrets}" in source
    assert '"accessToken"' in source
    assert '"refreshToken"' in source


def test_oauth_failure_responses_do_not_echo_provider_token_payloads():
    source = (
        ROOT / "masyg_extractor/integrations/auth_helper.py"
    ).read_text()

    assert '"details": response_json' not in source
    assert '"details": response_data' not in source
    assert '"access_token": "new_access_token"' not in source
    assert '"provider_error": response_json.get("error")' in source
    assert '"provider_error": response_data.get("error")' in source


def test_migration_is_dry_run_by_default_and_never_prints_secret_values():
    source = (
        ROOT / "scripts/migrate_accounting_integration_secrets.py"
    ).read_text()

    assert 'action="store_true"' in source
    assert "secret_values_output=NEVER" in source
    assert "ref.update(" in source
    assert "if args.apply:" in source
    assert "print(runtime" not in source
    assert "print(token_data" not in source
