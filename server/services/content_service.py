from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from server.models import (
    Batch,
    ContentItem,
    ContentVersion,
    FormatStatus,
    Project,
    PublishStatus,
    ReviewStatus,
)


MAX_TITLE_LENGTH = 500
MAX_BODY_LENGTH = 100_000


def _format_status(content: Mapping[str, Any]) -> FormatStatus:
    required = ("external_id", "title", "body")
    if any(key not in content or content[key] is None for key in required):
        return FormatStatus.INCOMPLETE
    if not all(isinstance(content[key], str) for key in required):
        return FormatStatus.INVALID
    if any(not content[key].strip() for key in required):
        return FormatStatus.INCOMPLETE
    if len(content["external_id"]) > 200 or len(content["title"]) > MAX_TITLE_LENGTH:
        return FormatStatus.INVALID
    if len(content["body"]) > MAX_BODY_LENGTH:
        return FormatStatus.INVALID
    if "payload" in content and not isinstance(content["payload"], Mapping):
        return FormatStatus.INVALID
    return FormatStatus.PASSED


def validate_content_format(title: Any, body: Any) -> tuple[str, str]:
    candidate = {"external_id": "validated", "title": title, "body": body, "payload": {}}
    if _format_status(candidate) is not FormatStatus.PASSED:
        raise ValueError("Content format requires trimmed non-empty title/body within length limits")
    return title.strip(), body.strip()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _coerce_format_status(value: Any) -> FormatStatus:
    if isinstance(value, FormatStatus):
        return value
    if isinstance(value, str):
        return FormatStatus(value)
    raise ValueError("format_status must be a valid FormatStatus value")


def submit_batch(
    session: Session,
    *,
    project_id: int,
    supplier_id: str,
    name: str,
    contents: Sequence[Mapping[str, Any]],
    format_status: FormatStatus | str | None = None,
    import_token: str | None = None,
    commit: bool = True,
) -> Batch:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} does not exist")
    if not supplier_id.strip() or not name.strip():
        raise ValueError("supplier_id and name are required")
    normalized_import_token = import_token.strip() if import_token is not None else None
    if import_token is not None and not normalized_import_token:
        raise ValueError("import_token cannot be blank")

    batch = Batch(
        project=project,
        supplier_id=supplier_id.strip(),
        name=name.strip(),
        import_token=normalized_import_token,
    )
    for content in contents:
        explicit_status = content.get("format_status", format_status)
        status = _coerce_format_status(explicit_status) if explicit_status is not None else _format_status(content)
        item = ContentItem(
            project=project,
            batch=batch,
            external_id=_text(content.get("external_id")),
            title=_text(content.get("title")),
            format_status=status,
            review_status=ReviewStatus.NOT_STARTED,
            publish_status=PublishStatus.NOT_READY,
        )
        ContentVersion(
            content_item=item,
            version=1,
            source="SUPPLIER",
            title=_text(content.get("title")),
            body=_text(content.get("body")),
            payload=dict(content.get("payload", {})) if isinstance(content.get("payload", {}), Mapping) else {},
        )

    session.add(batch)
    if commit:
        session.commit()
        session.refresh(batch)
    else:
        session.flush()
    return batch
