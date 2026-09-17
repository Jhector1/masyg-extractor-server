#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass

from firebase_admin import firestore

from masyg_extractor.firebase.firebase_init import firebase_init
from masyg_extractor.integrations.accounting.shared.integration_secret_crypto import (
    IntegrationSecretConfigurationError,
    IntegrationSecretDecryptionError,
    unseal_token_data,
)


PROVIDERS = ("quickbooks", "xero")


@dataclass
class MigrationStats:
    users_scanned: int = 0
    records_found: int = 0
    plaintext_records: int = 0
    encrypted_records: int = 0
    migrated_records: int = 0
    failures: int = 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Encrypt legacy plaintext QuickBooks/Xero tokenData records. "
            "Dry-run is the default."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite plaintext tokenData records.",
    )
    args = parser.parse_args()

    firebase_init()
    db = firestore.client()
    stats = MigrationStats()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"mode={mode}")
    print("providers=quickbooks,xero")
    print("secret_values_output=NEVER")

    for user_snapshot in db.collection("users").stream():
        if not user_snapshot.exists:
            continue

        stats.users_scanned += 1
        user_id = user_snapshot.id

        for provider in PROVIDERS:
            ref = (
                db.collection("users")
                .document(user_id)
                .collection("integrations")
                .document(provider)
            )
            snapshot = ref.get()
            if not snapshot.exists:
                continue

            document = snapshot.to_dict() or {}
            token_data = document.get("tokenData") or {}
            if not isinstance(token_data, dict) or not token_data:
                continue

            stats.records_found += 1

            try:
                _runtime, migrated = unseal_token_data(
                    user_id=user_id,
                    integration=provider,
                    token_data=token_data,
                )
            except (
                IntegrationSecretConfigurationError,
                IntegrationSecretDecryptionError,
            ) as exc:
                stats.failures += 1
                print(
                    "FAILED "
                    f"user={user_id} provider={provider} "
                    f"error_type={type(exc).__name__}"
                )
                continue

            if migrated is None:
                stats.encrypted_records += 1
                continue

            stats.plaintext_records += 1
            print(
                "NEEDS-MIGRATION "
                f"user={user_id} provider={provider}"
            )

            if args.apply:
                # The integration document already exists here.
                # Replace tokenData as a complete top-level field so legacy
                # plaintext children cannot survive a nested merge.
                ref.update(
                    {"tokenData": migrated}
                )
                stats.migrated_records += 1

    print("users_scanned=", stats.users_scanned)
    print("records_found=", stats.records_found)
    print("plaintext_records=", stats.plaintext_records)
    print("encrypted_records=", stats.encrypted_records)
    print("migrated_records=", stats.migrated_records)
    print("failures=", stats.failures)

    if stats.failures:
        return 2

    if not args.apply and stats.plaintext_records:
        print("result=DRY_RUN_MIGRATION_REQUIRED")
    elif args.apply:
        print("result=MIGRATION_COMPLETE")
    else:
        print("result=NO_PLAINTEXT_ACCOUNTING_TOKENS_FOUND")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
