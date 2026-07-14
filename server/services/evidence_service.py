from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from server.models import Asset, AssetKind, ContentItem, ContentVersion, TestCase, TestEvidence


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required and cannot be blank")
    return value.strip()


def _optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _tested_at(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError("tested_at must be an ISO date or datetime") from error


def create_asset(
    session: Session,
    content_item_id: int,
    *,
    asset_id: Optional[str] = None,
    external_id: Optional[str] = None,
    kind: AssetKind | str,
    filename: str,
    storage_key: Optional[str] = None,
    media_filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Asset:
    if session.get(ContentItem, content_item_id) is None:
        raise ValueError(f"Content item {content_item_id} does not exist")
    stable_id = _required(asset_id or external_id, "asset_id or external_id")
    safe_filename = _required(filename, "filename")
    if size_bytes is not None and size_bytes < 0:
        raise ValueError("size_bytes cannot be negative")
    try:
        asset_kind = kind if isinstance(kind, AssetKind) else AssetKind(_required(kind, "kind").upper())
    except ValueError as error:
        raise ValueError("kind must be a supported AssetKind") from error
    existing = session.scalar(select(Asset).where(
        Asset.content_item_id == content_item_id, Asset.asset_id == stable_id
    ))
    normalized_external_id = _optional(external_id)
    normalized_storage_key = _optional(storage_key or media_filename)
    values = {
        "external_id": normalized_external_id,
        "kind": asset_kind,
        "filename": safe_filename,
        "storage_key": normalized_storage_key,
        "mime_type": _optional(mime_type),
        "size_bytes": size_bytes,
        "asset_metadata": dict(metadata or {}),
    }
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ValueError("duplicate asset_id has conflicting data")
        return existing
    if normalized_external_id:
        by_external = session.scalar(select(Asset).where(
            Asset.content_item_id == content_item_id, Asset.external_id == normalized_external_id
        ))
        if by_external is not None:
            if by_external.asset_id != stable_id:
                raise ValueError("duplicate external_id has conflicting asset_id")
            return by_external
    asset = Asset(content_item_id=content_item_id, asset_id=stable_id, **values)
    session.add(asset)
    session.flush()
    return asset


def create_test_case(
    session: Session,
    content_item_id: int,
    content_version_id: int,
    *,
    external_test_case_id: str,
    claim: str,
    command: str,
    observed_result: str,
    city: Optional[str] = None,
    tested_at: Any = None,
    app_version: Optional[str] = None,
    device: Optional[str] = None,
    operating_system: Optional[str] = None,
    network_environment: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TestCase:
    item = session.get(ContentItem, content_item_id)
    version = session.get(ContentVersion, content_version_id)
    if item is None:
        raise ValueError(f"Content item {content_item_id} does not exist")
    if version is None:
        raise ValueError(f"Content version {content_version_id} does not exist")
    if version.content_item_id != content_item_id:
        raise ValueError("content/version ownership mismatch")
    stable_id = _required(external_test_case_id, "external_test_case_id")
    values = {
        "content_version_id": content_version_id,
        "claim": _required(claim, "claim"),
        "command": _required(command, "command"),
        "observed_result": _required(observed_result, "observed_result"),
        "city": _optional(city),
        "tested_at": _tested_at(tested_at),
        "app_version": _optional(app_version),
        "device": _optional(device),
        "operating_system": _optional(operating_system),
        "network_environment": _optional(network_environment),
        "test_metadata": dict(metadata or {}),
    }
    existing = session.scalar(select(TestCase).where(
        TestCase.content_item_id == content_item_id,
        TestCase.external_test_case_id == stable_id,
    ))
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ValueError("duplicate external_test_case_id has conflicting data")
        return existing
    test_case = TestCase(content_item_id=content_item_id, external_test_case_id=stable_id, **values)
    session.add(test_case)
    session.flush()
    return test_case


def attach_evidence(session: Session, test_case_id: int, asset_id: int) -> TestEvidence:
    test_case = session.get(TestCase, test_case_id)
    asset = session.get(Asset, asset_id)
    if test_case is None:
        raise ValueError(f"Test case {test_case_id} does not exist")
    if asset is None:
        raise ValueError(f"Asset {asset_id} does not exist")
    if test_case.content_item_id != asset.content_item_id:
        raise ValueError("evidence asset must belong to the same content")
    existing = session.scalar(select(TestEvidence).where(
        TestEvidence.test_case_id == test_case_id, TestEvidence.asset_id == asset_id
    ))
    if existing is not None:
        return existing
    evidence = TestEvidence(test_case=test_case, asset=asset)
    session.add(evidence)
    session.flush()
    return evidence


def _asset_manifest(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "asset_id": asset.asset_id,
        "external_id": asset.external_id,
        "kind": asset.kind.value,
        "filename": asset.filename,
        "storage_key": asset.storage_key,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "metadata": dict(asset.asset_metadata or {}),
    }


def _test_case_manifest(test_case: TestCase) -> dict[str, Any]:
    return {
        "id": test_case.id,
        "test_case_id": test_case.external_test_case_id,
        "external_test_case_id": test_case.external_test_case_id,
        "content_version_id": test_case.content_version_id,
        "claim": test_case.claim,
        "command": test_case.command,
        "observed_result": test_case.observed_result,
        "city": test_case.city,
        "tested_at": test_case.tested_at.isoformat() if test_case.tested_at else None,
        "app_version": test_case.app_version,
        "device": test_case.device,
        "operating_system": test_case.operating_system,
        "network_environment": test_case.network_environment,
        "metadata": dict(test_case.test_metadata or {}),
        "evidence_asset_ids": [binding.asset.asset_id for binding in test_case.evidence],
        "evidence_assets": [_asset_manifest(binding.asset) for binding in test_case.evidence],
        "evidence": [_asset_manifest(binding.asset) for binding in test_case.evidence],
    }


def list_content_test_cases(
    session: Session, content_item_id: int, *, content_version_id: Optional[int] = None
) -> list[dict[str, Any]]:
    if session.get(ContentItem, content_item_id) is None:
        raise ValueError(f"Content item {content_item_id} does not exist")
    statement = select(TestCase).where(TestCase.content_item_id == content_item_id)
    if content_version_id is not None:
        version = session.get(ContentVersion, content_version_id)
        if version is None or version.content_item_id != content_item_id:
            raise ValueError("content/version ownership mismatch")
        statement = statement.where(TestCase.content_version_id == content_version_id)
    records = session.scalars(
        statement.options(selectinload(TestCase.evidence).selectinload(TestEvidence.asset)).order_by(TestCase.id)
    ).all()
    return [_test_case_manifest(record) for record in records]
