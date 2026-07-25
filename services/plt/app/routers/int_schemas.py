
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class WebhookSubscriptionCreate(BaseModel):
    event_type: str = Field(min_length=1)  # e.g. 'appointment.*', 'result.final', 'invoice.finalized'
    url: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)


class WebhookSubscriptionOut(BaseModel):
    id: UUID
    event_type: str
    url: str
    secret_key: str
    active: bool


class HL7MessagePayload(BaseModel):
    message_text: str = Field(min_length=1)


class HL7MessageResponse(BaseModel):
    status: str
    parsed_segments: list[str] = []
    error_message: Optional[str] = None


class MockChargeRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = "INR"
    payment_method_id: str = Field(min_length=1)


class MockChargeResponse(BaseModel):
    id: UUID
    amount: float
    status: str  # 'authorized', 'captured', 'failed'
    transaction_reference: str
