"""Pure dashboard analytics aggregation.

This module intentionally has no Firebase/Redis imports so analytics math can be
unit-tested without infrastructure.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping


def safe_capitalize(val: object, default: str = "") -> str:
    s = str(val or "").strip()
    return s[:1].upper() + s[1:].lower() if s else default


def as_number(value: object, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        if isinstance(value, str):
            normalized = value.strip().replace(",", "").replace("$", "")
            if not normalized:
                return default
            return float(normalized)
        return float(value)
    except (TypeError, ValueError):
        return default


def line_total(line_item: Mapping[str, Any]) -> float:
    quantity = as_number(line_item.get("quantity"), default=0.0)
    unit_price = as_number(line_item.get("unit_price"), default=0.0)
    return quantity * unit_price


def month_from_metadata(metadata: Mapping[str, Any]) -> str:
    upload_time = metadata.get("upload_time")
    if isinstance(upload_time, datetime):
        return upload_time.strftime("%Y-%m")
    if upload_time:
        try:
            return datetime.fromisoformat(str(upload_time).replace("Z", "+00:00")).strftime("%Y-%m")
        except (TypeError, ValueError):
            pass
    return "unknown"


def aggregate_group_files(groups: Iterable[tuple[Mapping[str, Any], Iterable[Mapping[str, Any]]]]) -> dict:
    """Aggregate persisted group/file dictionaries into the dashboard contract."""
    monthly_uploads: dict[str, int] = {}
    total_spending_by_month: dict[str, float] = {}
    top_vendors: dict[str, int] = {}
    category_breakdown: dict[str, float] = {}
    total_files = 0
    successful_files = 0

    for group_data, files in groups:
        metadata = (group_data or {}).get("metadata") or {}
        month_key = month_from_metadata(metadata)
        file_list = list(files)

        # Persisted file documents are authoritative. Metadata file lists can be
        # stale after trash/purge/restore operations.
        monthly_uploads[month_key] = monthly_uploads.get(month_key, 0) + len(file_list)

        for file_data in file_list:
            file_data = file_data or {}
            total_files += 1
            if file_data.get("error"):
                continue
            successful_files += 1

            # Current extractor schema owns vendor_name. Keep vendor as a legacy
            # fallback for older persisted documents.
            vendor = file_data.get("vendor_name") or file_data.get("vendor")
            if vendor:
                vendor_name = str(vendor).strip()
                if vendor_name:
                    top_vendors[vendor_name] = top_vendors.get(vendor_name, 0) + 1

            file_total = 0.0
            for line_item in file_data.get("line_items") or []:
                if not isinstance(line_item, Mapping):
                    continue
                amount = line_total(line_item)
                file_total += amount

                category = safe_capitalize(line_item.get("category"), default="Uncategorized")
                category_breakdown[category] = round(
                    category_breakdown.get(category, 0.0) + amount,
                    2,
                )

            total_spending_by_month[month_key] = round(
                total_spending_by_month.get(month_key, 0.0) + file_total,
                2,
            )

    extraction_accuracy = (successful_files / total_files * 100.0) if total_files else 0.0

    return {
        "monthly_uploads": monthly_uploads,
        "total_spending_by_month": total_spending_by_month,
        "top_vendors": sorted(top_vendors.items(), key=lambda item: item[1], reverse=True),
        "extraction_accuracy": extraction_accuracy,
        "category_breakdown": category_breakdown,
    }
