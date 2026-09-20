from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.core.text import normalize_content, normalize_lookup
from app.db.session import create_database_engine, create_session_factory
from app.models.author import Author
from app.models.category import Category
from app.models.dynasty import Dynasty
from app.models.poem import Poem
from app.models.user import UserRole
from app.repositories.users import UserRepository
from app.schemas.auth import RegisterRequest
from app.schemas.catalog import (
    AuthorCreate,
    CategoryCreate,
    CategoryType,
    DynastyCreate,
    PoemCreate,
)
from app.services.catalog import CatalogService

DYNASTIES = [
    {"name": "唐", "sort_order": 10},
    {"name": "宋", "sort_order": 20},
    {"name": "元", "sort_order": 30},
]

AUTHORS = [
    {"name": "李白", "dynasty": "唐", "bio": "盛唐诗人，号青莲居士。"},
    {"name": "孟浩然", "dynasty": "唐", "bio": "唐代山水田园诗人。"},
    {"name": "王之涣", "dynasty": "唐", "bio": "盛唐边塞诗人。"},
    {"name": "苏轼", "dynasty": "宋", "bio": "北宋文学家，号东坡居士。"},
    {"name": "李清照", "dynasty": "宋", "bio": "宋代婉约派词人。"},
    {"name": "马致远", "dynasty": "元", "bio": "元代杂剧家、散曲家。"},
]

CATEGORIES = [
    {"name": "诗", "type": CategoryType.WORK_TYPE, "sort_order": 10},
    {"name": "词", "type": CategoryType.WORK_TYPE, "sort_order": 20},
    {"name": "曲", "type": CategoryType.WORK_TYPE, "sort_order": 30},
    {"name": "五言绝句", "type": CategoryType.FORM, "sort_order": 10},
    {"name": "豪放", "type": CategoryType.STYLE, "sort_order": 10},
    {"name": "婉约", "type": CategoryType.STYLE, "sort_order": 20},
    {"name": "思乡", "type": CategoryType.THEME, "sort_order": 10},
]

POEMS = [
    {
        "title": "静夜思",
        "author": "李白",
        "dynasty": "唐",
        "content": "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。",
        "summary": "明月照入客居，抬头与低头之间，尽是故乡。",
        "categories": ["诗", "五言绝句"],
        "tags": ["明月", "思乡"],
    },
    {
        "title": "春晓",
        "author": "孟浩然",
        "dynasty": "唐",
        "content": "春眠不觉晓，处处闻啼鸟。\n夜来风雨声，花落知多少。",
        "summary": "春夜醒来，只从鸟声与落花里猜度一场风雨。",
        "categories": ["诗", "五言绝句"],
        "tags": ["春天"],
    },
    {
        "title": "登鹳雀楼",
        "author": "王之涣",
        "dynasty": "唐",
        "content": "白日依山尽，黄河入海流。\n欲穷千里目，更上一层楼。",
        "summary": "目力所及并非终点，再向高处便见更远的天地。",
        "categories": ["诗", "五言绝句"],
        "tags": ["登临"],
    },
    {
        "title": "水调歌头·明月几时有",
        "author": "苏轼",
        "dynasty": "宋",
        "content": (
            "明月几时有？把酒问青天。\n"
            "不知天上宫阙，今夕是何年。\n"
            "我欲乘风归去，又恐琼楼玉宇，高处不胜寒。\n"
            "起舞弄清影，何似在人间。"
        ),
        "summary": "借一轮明月写离合，也写旷达的人间选择。",
        "categories": ["词", "豪放"],
        "tags": ["明月", "中秋"],
    },
    {
        "title": "如梦令·常记溪亭日暮",
        "author": "李清照",
        "dynasty": "宋",
        "content": (
            "常记溪亭日暮，沉醉不知归路。\n"
            "兴尽晚回舟，误入藕花深处。\n"
            "争渡，争渡，惊起一滩鸥鹭。"
        ),
        "summary": "暮色、荷塘与归舟构成一段轻盈鲜明的记忆。",
        "categories": ["词", "婉约"],
        "tags": ["荷塘"],
    },
    {
        "title": "天净沙·秋思",
        "author": "马致远",
        "dynasty": "元",
        "content": "枯藤老树昏鸦，小桥流水人家，古道西风瘦马。\n夕阳西下，断肠人在天涯。",
        "summary": "极简的景物排列里，留下旅人孤行天涯的背影。",
        "categories": ["曲", "思乡"],
        "tags": ["秋思", "思乡"],
    },
]


async def seed_catalog(session: AsyncSession) -> dict[str, int]:
    dynasty_by_name: dict[str, Dynasty] = {}
    author_by_name: dict[str, Author] = {}
    category_by_name: dict[str, Category] = {}
    created = {"dynasties": 0, "authors": 0, "categories": 0, "poems": 0}

    service = CatalogService(session)
    for item in DYNASTIES:
        normalized = normalize_lookup(item["name"])
        dynasty = await service.dynasties.get_by_normalized_name(normalized)
        if dynasty is None:
            dynasty = await service.create_dynasty(
                DynastyCreate(
                    name=item["name"],
                    sort_order=int(item["sort_order"]),
                )
            )
            dynasty = await service.dynasties.get_by_normalized_name(normalized)
            created["dynasties"] += 1
        dynasty_by_name[item["name"]] = dynasty

    for item in AUTHORS:
        normalized = normalize_lookup(item["name"])
        author = await service.authors.get_by_normalized_name(normalized)
        if author is None:
            await service.create_author(
                AuthorCreate(
                    name=item["name"],
                    bio=item["bio"],
                    dynasty_id=dynasty_by_name[item["dynasty"]].id,
                )
            )
            author = await service.authors.get_by_normalized_name(normalized)
            created["authors"] += 1
        author_by_name[item["name"]] = author

    for item in CATEGORIES:
        category = await service.categories.get_by_normalized_name(
            normalize_lookup(item["name"]),
            item["type"].value,
        )
        if category is None:
            category = await service.create_category(
                CategoryCreate(
                    name=item["name"],
                    type=item["type"],
                    sort_order=int(item["sort_order"]),
                )
            )
            category = await service.categories.get_by_normalized_name(
                normalize_lookup(item["name"]),
                item["type"].value,
            )
            created["categories"] += 1
        category_by_name[item["name"]] = category

    for item in POEMS:
        author = author_by_name[item["author"]]
        normalized_content = normalize_content(item["content"])
        existing = await session.scalar(
            select(Poem.id).where(
                Poem.author_id == author.id,
                Poem.normalized_content == normalized_content,
            )
        )
        if existing is not None:
            continue
        poem = await service.create_poem(
            PoemCreate(
                title=item["title"],
                author_id=author.id,
                dynasty_id=dynasty_by_name[item["dynasty"]].id,
                content=item["content"],
                summary=item["summary"],
                category_ids=[category_by_name[name].id for name in item["categories"]],
                tag_names=item["tags"],
            )
        )
        await service.publish_poem(poem.id)
        created["poems"] += 1

    return created


async def seed_admin(session: AsyncSession, settings: Settings) -> bool:
    configured_values = (
        settings.seed_admin_email,
        settings.seed_admin_password,
        settings.seed_admin_display_name,
    )
    if settings.is_production:
        if any(configured_values):
            raise RuntimeError("SEED_ADMIN_* settings are not allowed in production")
        return False

    if not any(configured_values):
        return False
    if not all(configured_values):
        raise RuntimeError(
            "SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD and "
            "SEED_ADMIN_DISPLAY_NAME must be configured together"
        )

    assert settings.seed_admin_email is not None
    assert settings.seed_admin_password is not None
    assert settings.seed_admin_display_name is not None
    payload = RegisterRequest(
        email=settings.seed_admin_email,
        password=settings.seed_admin_password.get_secret_value(),
        display_name=settings.seed_admin_display_name,
    )

    users = UserRepository(session)
    existing = await users.get_by_email(payload.email.lower())
    if existing is not None:
        if existing.role != UserRole.ADMIN.value:
            raise RuntimeError("Configured seed admin email belongs to a non-admin user")
        return False

    await users.create(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=UserRole.ADMIN.value,
    )
    await session.commit()
    return True


async def main() -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        created = await seed_catalog(session)
        admin_created = await seed_admin(session, settings)
    await engine.dispose()
    print(f"Seed completed: catalog={created}, admin_created={admin_created}")


if __name__ == "__main__":
    asyncio.run(main())
