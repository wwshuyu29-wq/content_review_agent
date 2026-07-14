from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project
from .services.standard_package_service import load_standard_package, publish_standard_package


DEFAULT_PROJECT_CODE = "bdmap_xdxx_tech_review_2026"
DEFAULT_PROJECT_NAME = "百度地图小度想想科技媒体测评"
DEFAULT_CONTENT_TYPE = "TECH_MEDIA_REVIEW"


def seed_default_project(session: Session) -> Project:
    project = session.scalar(select(Project).where(Project.code == DEFAULT_PROJECT_CODE))
    if project is not None:
        return project

    project = Project(
        name=DEFAULT_PROJECT_NAME,
        code=DEFAULT_PROJECT_CODE,
        content_type=DEFAULT_CONTENT_TYPE,
        description="百度地图小度想想科技媒体亲测、实测和产品评测审核项目。",
    )
    session.add(project)
    session.flush()
    root = Path(__file__).resolve().parents[1] / "data" / "standards"
    package = load_standard_package(root, DEFAULT_PROJECT_CODE)
    publish_standard_package(session, project.id, package)
    return project
