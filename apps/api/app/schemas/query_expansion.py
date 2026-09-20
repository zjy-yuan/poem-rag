from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import normalize_lookup


class QueryExpansionConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    triggers: list[str] = Field(min_length=1)
    expansions: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("概念名称不能为空")
        return value

    @field_validator("triggers", "expansions")
    @classmethod
    def terms_must_be_unique_and_non_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("触发词和扩展词不能为空")
        normalized = [normalize_lookup(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("同一概念中的触发词和扩展词不能重复")
        return values


class QueryExpansionLexicon(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=100)
    concepts: list[QueryExpansionConcept] = Field(min_length=1)
    known_authors: list[str] = Field(min_length=1)
    known_dynasties: list[str] = Field(min_length=1)
    poetic_intent_terms: list[str] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def version_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("词典版本不能为空")
        return value

    @field_validator("known_authors", "known_dynasties", "poetic_intent_terms")
    @classmethod
    def terms_must_be_unique_and_non_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("实体和意图词不能为空")
        normalized = [normalize_lookup(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("实体和意图词不能重复")
        return values

    @model_validator(mode="after")
    def concept_names_must_be_unique(self) -> Self:
        normalized = [normalize_lookup(concept.name) for concept in self.concepts]
        if len(set(normalized)) != len(normalized):
            raise ValueError("概念名称不能重复")
        return self
