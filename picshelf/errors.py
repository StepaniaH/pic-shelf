"""Stable service errors with localized messages and actionable recovery."""
from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, code: str, detail: str, *, status: int = 400,
                 retryable: bool = False, next_step: str, objects: dict | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.retryable = retryable
        self.next_step = next_step
        self.objects = objects or {}

    def as_dict(self) -> dict:
        return {"detail": self.detail, "error": {
            "code": self.code, "retryable": self.retryable,
            "next_step": self.next_step, "objects": self.objects,
        }}
