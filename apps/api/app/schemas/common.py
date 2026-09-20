from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class PageResult[T]:
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def meta(self) -> PaginationMeta:
        total_pages = (self.total + self.page_size - 1) // self.page_size if self.total else 0
        return PaginationMeta(
            page=self.page,
            page_size=self.page_size,
            total=self.total,
            total_pages=total_pages,
        )
