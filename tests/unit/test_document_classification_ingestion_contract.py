from pathlib import Path

from masyg_extractor.documents.document_types import (
    DocumentType,
    normalize_extracted_document,
)

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_missing_or_unknown_document_type_normalizes_to_other():
    assert (
        normalize_extracted_document(
            {
                "documentType": None,
                "line_items": [{"description": "table row"}],
            }
        )["documentType"]
        == DocumentType.OTHER.value
    )

    assert (
        normalize_extracted_document(
            {
                "documentType": "novel",
                "line_items": [{"description": "chapter"}],
            }
        )["documentType"]
        == DocumentType.OTHER.value
    )


def test_processing_rejects_other_before_firestore_persistence():
    source = read("masyg_extractor/services/processing.py")

    normalize = source.index(
        "json_content = normalize_extracted_document(json_content)"
    )
    reject = source.index(
        'json_content.get("documentType") == DocumentType.OTHER.value'
    )
    file_write = source.index("async def update_firestore_file(")

    assert normalize < reject < file_write
    assert '"error": "Unsupported document type"' in source
    assert '"stage": "Document classification"' in source


def test_regex_fallback_passes_through_canonical_document_type_owner():
    source = read("masyg_extractor/services/processing.py")

    fallback = source.index(
        'json_content = {\n'
        '                "documentType": None,'
    )
    normalize = source.index(
        "json_content = normalize_extracted_document(json_content)",
        fallback,
    )

    assert fallback < normalize


def test_classification_error_is_preserved_by_both_processing_paths():
    source = read("masyg_extractor/services/processing.py")

    assert source.count('if parsed_content.get("error"):') >= 2
    assert 'return parsed_content, uploaded_file.filename' in source
    assert 'return idx, file_id, parsed_content' in source


def test_prompt_does_not_invite_generic_pdf_fabrication():
    source = read("masyg_extractor/services/file_extractor_service.py")

    assert (
        "Use other for content that is not a supported business or financial document"
        in source
    )
    assert "books, articles, manuals, letters, and general prose" in source
    assert "do not invent transaction data" in source
    assert "Never fabricate line items" in source
