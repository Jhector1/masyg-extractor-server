import pytest

from masyg_extractor.services.analytics import aggregate_group_files, line_total


def test_line_total_uses_quantity_times_unit_price():
    assert line_total({"quantity": "3", "unit_price": "12.50"}) == 37.5
    assert line_total({"quantity": "2", "unit_price": "$1,250.00"}) == 2500.0


def test_dashboard_analytics_use_persisted_file_schema_and_real_spending():
    groups = [
        (
            {
                "metadata": {
                    "upload_time": "2026-09-11T10:00:00+00:00",
                    # Deliberately stale: aggregation must count actual docs below.
                    "files": [{"filename": "stale.pdf"}],
                }
            },
            [
                {
                    "vendor_name": "Vendor A",
                    "line_items": [
                        {"category": "materials", "quantity": 2, "unit_price": "15.00"},
                        {"category": "materials", "quantity": 1, "unit_price": "5.50"},
                    ],
                },
                {
                    "vendor_name": "Vendor A",
                    "line_items": [
                        {"category": None, "quantity": "3", "unit_price": "10"},
                    ],
                },
                {"error": "extract failed"},
            ],
        )
    ]

    analytics = aggregate_group_files(groups)

    assert analytics["monthly_uploads"] == {"2026-09": 3}
    assert analytics["total_spending_by_month"] == {"2026-09": 65.5}
    assert analytics["top_vendors"] == [("Vendor A", 2)]
    assert analytics["category_breakdown"] == {
        "Materials": 35.5,
        "Uncategorized": 30.0,
    }
    assert analytics["extraction_accuracy"] == pytest.approx(200 / 3)


def test_analytics_support_legacy_vendor_field():
    analytics = aggregate_group_files(
        [
            (
                {"metadata": {"upload_time": "2026-09-01"}},
                [{"vendor": "Legacy Vendor", "line_items": []}],
            )
        ]
    )
    assert analytics["top_vendors"] == [("Legacy Vendor", 1)]
