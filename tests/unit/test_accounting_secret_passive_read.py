import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKEN_REPOSITORY = ROOT / 'masyg_extractor/integrations/accounting/shared/token_repository.py'

def _generic_read_function():
    tree = ast.parse(TOKEN_REPOSITORY.read_text(), filename=str(TOKEN_REPOSITORY))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_integration_token')

def test_generic_accounting_secret_read_has_no_firestore_write():
    function = _generic_read_function()
    writes = [node for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {'set', 'update', 'delete', 'create'}]
    assert writes == []

def test_generic_read_still_owns_and_decrypts_provider_token():
    source = TOKEN_REPOSITORY.read_text()
    function = _generic_read_function()
    calls = list(ast.walk(function))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'collection' and len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'integrations' for node in calls)
    segment = ast.get_source_segment(source, function) or ''
    assert 'unseal_token_data(' in segment
    assert 'return runtime_token_data' in segment

def test_encrypted_write_and_explicit_migration_remain_available():
    repo_source = TOKEN_REPOSITORY.read_text()
    migration = (ROOT / 'scripts/migrate_accounting_integration_secrets.py').read_text()
    assert 'seal_token_data(' in repo_source
    assert '{"tokenData": stored_token_data}' in repo_source
    assert 'unseal_token_data(' in migration
    assert '--apply' in migration
