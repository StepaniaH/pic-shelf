"""Additive metadata and reviewable catalog-edit endpoints."""
from typing import Literal

from fastapi import APIRouter
from pydantic import StrictBool, BaseModel, ConfigDict, Field, StrictInt

from .. import entity_edits, metadata
from ..access import AccessContext
from ..errors import ServiceError
from ..i18n import t
from .types import Identifier, BodyIdentifier
from .deps import Db, User

router = APIRouter(tags=["metadata"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowBody(StrictBody):
    image_ids: list[BodyIdentifier] = Field(min_length=1, max_length=1000)
    state: Literal["inbox", "done"]


class SourceBody(StrictBody):
    original_filename: str | None = Field(default=None, max_length=1000)
    collected_at: str | None = None
    created_time: str | None = None
    url: str | None = Field(default=None, max_length=4096)
    note: str | None = Field(default=None, max_length=10000)


class ClaimBody(StrictBody):
    name: str = Field(min_length=1, max_length=300)
    origin: Literal["manual", "imported", "suggested"] = "imported"
    source_ref: str = Field(default="", max_length=1000)


@router.get("/images/{image_id}/metadata")
def image_metadata(image_id: Identifier, conn: Db, user: User):
    return metadata.get(conn, AccessContext(user["id"]), image_id)


@router.post("/metadata/workflow")
def mark_workflow(body: WorkflowBody, conn: Db, user: User):
    return metadata.mark(conn, AccessContext(user["id"]), body.image_ids, body.state)


@router.post("/images/{image_id}/sources")
def add_source(image_id: Identifier, body: SourceBody, conn: Db, user: User):
    return metadata.add_source(conn, AccessContext(user["id"]), image_id, **body.model_dump())


@router.post("/images/{image_id}/tag-claims")
def add_claim(image_id: Identifier, body: ClaimBody, conn: Db, user: User):
    return metadata.add_tag_claim(conn, AccessContext(user["id"]), image_id, **body.model_dump())


@router.post("/tag-claims/{claim_id}/confirm")
def confirm_claim(claim_id: Identifier, conn: Db, user: User):
    return metadata.confirm_tag_claim(conn, AccessContext(user["id"]), claim_id)


class AliasBody(StrictBody):
    kind: Literal["tag", "artist"]
    entity_id: BodyIdentifier
    alias: str = Field(min_length=1, max_length=300)


class MergeBody(StrictBody):
    kind: Literal["tag", "artist"]
    source_id: BodyIdentifier
    target_id: BodyIdentifier
    image_ids: list[BodyIdentifier] = Field(min_length=1, max_length=1000)


class ConfirmBody(StrictBody):
    confirm: StrictBool = False


@router.post("/metadata/aliases/preview")
def preview_alias(body: AliasBody, conn: Db, user: User):
    return entity_edits.preview_alias(conn, AccessContext(user["id"]), **body.model_dump())


@router.post("/metadata/merges/preview")
def preview_merge(body: MergeBody, conn: Db, user: User):
    return entity_edits.preview_merge(conn, AccessContext(user["id"]), **body.model_dump())


@router.get("/metadata/plans/{plan_id}")
def view_plan(plan_id: str, conn: Db, user: User):
    import json
    plan = entity_edits.get_plan(conn, AccessContext(user["id"]), plan_id)
    return {"id": plan_id, "expires_at": plan["expires_at"], "snapshot": json.loads(plan["snapshot"]),
            "result": json.loads(plan["result"]) if plan["result"] else None}


@router.post("/metadata/plans/{plan_id}/execute")
def execute_plan(plan_id: str, body: ConfirmBody, conn: Db, user: User):
    if not body.confirm:
        raise ServiceError("confirmation_required", t("执行需要 confirm=true 明确确认"),
                           next_step=t("请先查看预演的名称和图片关联范围"))
    return entity_edits.execute(conn, AccessContext(user["id"]), plan_id)
