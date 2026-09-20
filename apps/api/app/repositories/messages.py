from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.message import Message, MessageCitation


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        status: str,
        model: str | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
            model=model,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_for_conversation(self, conversation_id: int) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .options(selectinload(Message.citations))
            .order_by(Message.id)
        )
        return list(result.scalars().unique())

    async def add_citations(
        self,
        message: Message,
        citations: Sequence[MessageCitation],
    ) -> None:
        self.session.add_all(citations)
        await self.session.flush()

    async def get(
        self,
        *,
        message_id: int,
        conversation_id: int,
    ) -> Message | None:
        result = await self.session.execute(
            select(Message)
            .where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
            )
            .options(selectinload(Message.citations))
        )
        return result.scalar_one_or_none()
