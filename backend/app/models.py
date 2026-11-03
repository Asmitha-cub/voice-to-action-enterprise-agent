from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Action = Literal[
    'create_task', 'list_tasks', 'search_contacts',
    'ticket_summary', 'search_knowledge', 'compose_document',
    'send_message', 'unknown',
]


class TextCommandRequest(BaseModel):
    command: str = Field(min_length=3, max_length=1000)


class ActionPlan(BaseModel):
    action: Action
    summary: str
    parameters: dict[str, str] = Field(default_factory=dict)
    requires_confirmation: bool
    status: Literal['planned', 'completed', 'needs_clarification', 'failed']
    risk: Literal['low', 'medium', 'high'] = 'low'


class CommandResponse(BaseModel):
    transcript: str
    plan: ActionPlan
    execution_id: str | None = None
    result: dict | None = None
    activity: list[str] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    execution_id: str = Field(min_length=8, max_length=64)
    approved: bool


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    team: str = Field(default='General', max_length=120)
    phone: str = Field(default='', max_length=40)


class ComposeRequest(BaseModel):
    doc_type: str = Field(min_length=2, max_length=80)
    recipient: str = Field(default='', max_length=200)
    instructions: str = Field(default='', max_length=2000)
    channels: str = Field(default='', max_length=120)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    recipient: str = Field(default='', max_length=200)
    channels: str = Field(default='', max_length=120)


class ResolveRequest(BaseModel):
    recipient: str = Field(default='', max_length=400)
