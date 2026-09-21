from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def extract_route(source: str) -> str:
    start = source.index("async def extract_data(")
    end = source.index('@router.post("/update-change-log")', start)
    return source[start:end]


def test_dropbox_source_notifies_only_after_canonical_success():
    route = extract_route(
        read("masyg_extractor/routes/data_extractor_routes.py")
    )

    assert 'source: str = Form("local")' in route
    assert 'normalized_source not in {"local", "dropbox"}' in route

    ingest = route.index("result = await ingest_documents(")
    guard = route.index(
        'if normalized_source == "dropbox" and not result.get("error"):',
        ingest,
    )
    notify = route.index("await publish_user_notification(", guard)
    returned = route.index("return result", notify)

    assert ingest < guard < notify < returned
    assert 'source="dropbox"' in route
    assert 'type="document.imported"' in route
    assert 'severity="success"' in route
    assert 'result.get("group_id")' in route
    assert 'metadata.get("file_count")' in route
    assert 'dedupe_key=f"dropbox:document.imported:{group_id}"' in route


def test_local_upload_stays_outside_dropbox_notification_branch():
    route = extract_route(
        read("masyg_extractor/routes/data_extractor_routes.py")
    )
    assert route.count('normalized_source == "dropbox"') == 1


def test_notification_failure_is_non_fatal_to_successful_upload():
    route = extract_route(
        read("masyg_extractor/routes/data_extractor_routes.py")
    )

    notify = route.index("await publish_user_notification(")
    warning = route.index("Dropbox import notification failed", notify)
    returned = route.index("return result", warning)

    assert notify < warning < returned
