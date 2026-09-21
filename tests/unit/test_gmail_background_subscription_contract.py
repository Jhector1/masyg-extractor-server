from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_gmail_background_entitlement_reuses_canonical_rule():
    subscription = (
        ROOT
        / "masyg_extractor/services/subscription_access.py"
    ).read_text()
    service = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail/service.py"
    ).read_text()

    assert (
        "async def user_has_active_subscription"
        in subscription
    )
    assert (
        "return has_active_subscription("
        in subscription
    )
    assert (
        "user_has_active_subscription"
        in service
    )
    assert "isSubscribed" not in service
