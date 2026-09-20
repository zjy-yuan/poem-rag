from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, user_id: int, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(
                Conversation.last_message_at.desc(),
                Conversation.updated_at.desc(),
                Conversation.id.desc(),
            )
            .limit(limit)
        )
        return list(result.scalars())

    async def get_for_user(
        self,
        conversation_id: int,
        user_id: int,
    ) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, conversation: Conversation) -> None:
        await self.session.delete(conversation)

    async def touch(self, conversation: Conversation) -> None:
        conversation.last_message_at = datetime.now(UTC)
        await self.session.flush()
