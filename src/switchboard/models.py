"""Normalized records shared by the mock store and the live API parsers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Contact(BaseModel):
    id: str
    tenant_id: str
    email: str
    first_name: str
    last_name: str
    company: str = ""
    phone: str = ""


class Deal(BaseModel):
    id: str
    tenant_id: str
    name: str
    amount_cents: int
    stage: str
    contact_id: str = ""


class Note(BaseModel):
    id: str
    tenant_id: str
    contact_id: str
    body: str
    created_at: str


class Campaign(BaseModel):
    id: str
    tenant_id: str
    platform: str
    name: str
    status: str


class MetricDay(BaseModel):
    tenant_id: str
    campaign_id: str
    date: str
    impressions: int
    clicks: int
    spend_cents: int
    conversions: int


class Page(BaseModel):
    id: str
    tenant_id: str
    title: str
    url: str
    body: str


class Member(BaseModel):
    tenant_id: str
    email: str
    name: str
    role: str


class Task(BaseModel):
    id: str
    tenant_id: str
    name: str
    status: str
    assignee_email: str
    due_date: str = ""
    notes: str = ""


class Channel(BaseModel):
    tenant_id: str
    name: str


class SlackMessage(BaseModel):
    tenant_id: str
    channel: str
    text: str
    ts: str


class Workspace(BaseModel):
    id: str
    name: str
    trade: str
    city: str
    account_lead: str
    retainer_cents: int
    summary: str


class Seed(BaseModel):
    workspaces: list[Workspace] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    deals: list[Deal] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    campaigns: list[Campaign] = Field(default_factory=list)
    metrics: list[MetricDay] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
