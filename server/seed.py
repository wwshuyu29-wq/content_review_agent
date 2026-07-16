from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, RuleVersion
from .services.standard_package_service import load_standard_package, publish_standard_package

DEFAULT_PROJECT_CODE = "bdmap_xdxx_tech_review_2026"
DEFAULT_PROJECT_NAME = "百度地图小度想想"
DEFAULT_CONTENT_TYPE = "TECH_MEDIA_REVIEW"
DEFAULT_PACKAGE_VERSION = "1.3"


def seed_default_project(session: Session) -> Project:
    project = session.scalar(select(Project).where(Project.code == DEFAULT_PROJECT_CODE))
    root = Path(__file__).resolve().parents[1] / "data" / "standards"
    package = load_standard_package(root, DEFAULT_PROJECT_CODE, DEFAULT_PACKAGE_VERSION)
    if project is None:
        project = Project(
            name=DEFAULT_PROJECT_NAME,
            code=DEFAULT_PROJECT_CODE,
            content_type=DEFAULT_CONTENT_TYPE,
            description="百度地图小度想想亲测、实测和产品评测审核项目。",
        )
        session.add(project)
        session.flush()
    elif project.content_type != DEFAULT_CONTENT_TYPE:
        raise ValueError("default project has incompatible content_type")
    try:
        publish_standard_package(session, project.id, package)
    except ValueError as error:
        if "standard package digest mismatch for existing package_version" not in str(error):
            raise
        existing = session.scalar(
            select(RuleVersion).where(
                RuleVersion.project_id == project.id,
                RuleVersion.package_version == package.metadata.version,
            )
        )
        if existing is None:
            raise
        project.current_rule_version = existing
    return project
