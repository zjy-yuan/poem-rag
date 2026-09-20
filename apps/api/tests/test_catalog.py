from __future__ import annotations

from typing import Any

from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Catalog tester",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _promote_user(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.email == email).values(role=UserRole.ADMIN.value)
        )
        await session.commit()


def _admin_client(client: TestClient) -> tuple[dict[str, str], dict[str, Any]]:
    email = "catalog-admin@example.com"
    body = _register(client, email)
    portal = client.portal
    assert portal is not None
    portal.call(_promote_user, client.app.state.session_factory, email)
    token = body["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}, body


def test_admin_routes_reject_non_admin(client: TestClient) -> None:
    body = _register(client, "reader@example.com")
    token = body["data"]["access_token"]

    response = client.post(
        "/api/v1/admin/dynasties",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Tang"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_catalog_admin_lifecycle(client: TestClient) -> None:
    headers, _ = _admin_client(client)

    dynasty_response = client.post(
        "/api/v1/admin/dynasties",
        headers=headers,
        json={"name": "Tang", "sort_order": 10},
    )
    assert dynasty_response.status_code == 201
    dynasty = dynasty_response.json()["data"]

    author_response = client.post(
        "/api/v1/admin/authors",
        headers=headers,
        json={
            "name": "Li Bai",
            "aliases": ["Taibai"],
            "bio": "Tang poet",
            "dynasty_id": dynasty["id"],
        },
    )
    assert author_response.status_code == 201
    author = author_response.json()["data"]

    category_response = client.post(
        "/api/v1/admin/categories",
        headers=headers,
        json={"name": "Poem", "type": "work_type", "sort_order": 10},
    )
    assert category_response.status_code == 201
    category = category_response.json()["data"]

    poem_response = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Quiet Night",
            "author_id": author["id"],
            "dynasty_id": dynasty["id"],
            "content": "Moonlight before my bed.\nI lower my head and think of home.",
            "summary": "A traveler sees moonlight and remembers home.",
            "category_ids": [category["id"]],
            "tag_names": ["moonlight", "homesickness"],
        },
    )
    assert poem_response.status_code == 201
    poem = poem_response.json()["data"]
    assert poem["status"] == "draft"
    assert poem["version_no"] == 1
    assert poem["deleted_at"] is None
    assert poem["categories"][0]["name"] == "Poem"
    assert {tag["name"] for tag in poem["tags"]} == {"moonlight", "homesickness"}

    public_list = client.get("/api/v1/poems").json()
    assert public_list["meta"]["total"] == 0
    assert client.get(f"/api/v1/poems/{poem['id']}").status_code == 404

    draft_probe = client.get("/api/v1/poems", params={"status": "draft"}).json()
    assert draft_probe["meta"]["total"] == 0

    updated_response = client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"title": "Moonlit Night", "version_no": 1},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["data"]["version_no"] == 2

    conflict_response = client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"title": "Stale edit", "version_no": 1},
    )
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "POEM_VERSION_CONFLICT"

    published_response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/publish",
        headers=headers,
    )
    assert published_response.status_code == 200
    published = published_response.json()["data"]
    assert published["status"] == "published"
    assert published["published_at"] is not None

    public_list = client.get("/api/v1/poems").json()
    assert public_list["meta"]["total"] == 1
    assert public_list["data"][0]["title"] == "Moonlit Night"

    public_detail = client.get(f"/api/v1/poems/{poem['id']}")
    assert public_detail.status_code == 200
    assert public_detail.json()["data"]["author_name"] == "Li Bai"
    assert public_detail.json()["data"]["dynasty_name"] == "Tang"

    search_response = client.get("/api/v1/search", params={"q": "Moonlight"})
    assert search_response.status_code == 200
    assert search_response.json()["meta"]["total"] == 1

    author_detail = client.get(f"/api/v1/authors/{author['id']}")
    assert author_detail.status_code == 200
    assert author_detail.json()["data"]["poems"][0]["title"] == "Moonlit Night"

    unpublish_response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/unpublish",
        headers=headers,
    )
    assert unpublish_response.status_code == 200
    assert unpublish_response.json()["data"]["published_at"] is None
    assert client.get(f"/api/v1/poems/{poem['id']}").status_code == 404

    delete_response = client.delete(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert client.get(f"/api/v1/poems/{poem['id']}").status_code == 404

    deleted_list = client.get(
        "/api/v1/admin/poems",
        headers=headers,
        params={"include_deleted": "true"},
    ).json()
    assert deleted_list["meta"]["total"] == 1
    assert deleted_list["data"][0]["status"] == "archived"
    assert deleted_list["data"][0]["deleted_at"] is not None

    restore_response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/restore",
        headers=headers,
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["data"]["status"] == "draft"
    assert restore_response.json()["data"]["version_no"] == 4

    dynasties = client.get("/api/v1/dynasties").json()["data"]
    categories = client.get("/api/v1/categories").json()["data"]
    assert dynasties[0]["name"] == "Tang"
    assert categories[0]["name"] == "Poem"