from typing import Literal
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    chunk_id: str
    quote: str = Field(min_length=8, max_length=1000)


class Claim(BaseModel):
    paper_id: str
    kind: Literal["background", "method", "experiment", "limitation", "comparison"]
    text: str = Field(min_length=2)
    evidence: list[Evidence] = Field(min_length=1)


class Analysis(BaseModel):
    claims: list[Claim] = Field(max_length=30)


class Plan(BaseModel):
    focus: str
    method_queries: list[str] = Field(min_length=1, max_length=5)
    experiment_queries: list[str] = Field(min_length=1, max_length=5)
    comparison_dimensions: list[str] = Field(min_length=1, max_length=8)


class Verdict(BaseModel):
    claim_index: int
    supported: bool
    reason: str


class Review(BaseModel):
    verdicts: list[Verdict]


class ReportDraft(BaseModel):
    title: str
    comparisons: list[Claim] = Field(max_length=10)
    open_questions: list[str] = Field(max_length=8)


class ReproductionPlan(BaseModel):
    feasible: bool
    reason: str
    scope: str
    limitations: list[str]
