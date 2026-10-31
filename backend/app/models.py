from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Report(RequestModel):
    message: str = Field(min_length=1, max_length=4000)
    job_id: str | None = Field(default=None, pattern=r"^JOB-\d{4}$")


class ApprovalDecision(RequestModel):
    decision: Literal["approved", "rejected"]
    actor: str = Field(min_length=2, max_length=100)


class RevisitConfirmation(RequestModel):
    selected_slot: str = Field(min_length=1, max_length=100)
    customer_confirmed: bool = Field(strict=True)
    technician_confirmed: bool = Field(strict=True)
    actor: str = Field(min_length=2, max_length=100)


class Actor(RequestModel):
    actor: str = Field(min_length=2, max_length=100)


class Completion(Actor):
    confirmed: bool = Field(strict=True)
    evidence: str = Field(min_length=5, max_length=2000)


class Escalation(Actor):
    reason: str = Field(min_length=5, max_length=2000)


class Reset(RequestModel):
    confirm: Literal[True]


class VoiceCall(RequestModel):
    recipient_phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
