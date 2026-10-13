"""Structured output schema for an assessment."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(StrictModel):
    url: str = Field(description='URL of the page the evidence was found on')
    quote: str = Field(description='Short verbatim quote or precise paraphrase from that page')


class Product(StrictModel):
    name: str
    vendor: str
    url: str
    category: str = Field(description='E.g. desktop application, web browser, mobile app, SaaS')
    platforms: list[str] = Field(description='E.g. Windows, macOS, Linux, Android, iOS, Web, Server')
    summary: str = Field(description='2-3 sentences describing the product')


class Overall(StrictModel):
    verdict: Literal['likely_eligible', 'blockers_found', 'insufficient_information']
    summary: str = Field(description='3-5 sentences: overall assessment for the applicant')
    key_risks: list[str] = Field(description='Most important risks or likely blockers, most severe first')


class CriterionAssessment(StrictModel):
    id: int = Field(description='Criterion number from the criteria list (1-21)')
    status: Literal['verified', 'not_possible', 'maybe']
    confidence: Literal['low', 'medium', 'high']
    comment: str = Field(description='Reasoning for the status, referring to the evidence')
    evidence: list[Evidence]
    open_questions: list[str] = Field(description='Questions the applicant must answer to resolve this criterion')


class Assessment(StrictModel):
    product: Product
    overall: Overall
    criteria: list[CriterionAssessment] = Field(description='Exactly one entry per criterion, ordered by id')


def assessment_json_schema() -> dict:
    return Assessment.model_json_schema()
