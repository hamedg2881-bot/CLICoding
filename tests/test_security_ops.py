from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings


ADMIN_TOKEN = "a" * 48


def test_admin_can_issue_list_and_revoke_user_tokens(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "security.sqlite3"),
        api_key=ADMIN_TOKEN,
    )
    client = TestClient(create_app(settings=settings))

    issued = client.post(
        "/api/v1/admin/tokens",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"owner_id": "alice", "label": "Alice workstation"},
    )
    assert issued.status_code == 201
    token = issued.json()
    assert token["owner_id"] == "alice"
    assert len(token["token"]) >= 40

    user_headers = {"Authorization": f"Bearer {token['token']}"}
    created = client.post("/api/v1/conversations", headers=user_headers)
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    history = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers=user_headers,
    )
    assert history.status_code == 200

    listing = client.get(
        "/api/v1/admin/tokens",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert listing.status_code == 200
    assert len(listing.json()["tokens"]) == 1
    assert "token" not in listing.json()["tokens"][0]
    assert token["token"] not in listing.text

    revoked = client.delete(
        f"/api/v1/admin/tokens/{token['id']}",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}

    rejected = client.get("/v1/models", headers=user_headers)
    assert rejected.status_code == 401


def test_dynamic_tokens_enable_authentication_and_cannot_issue_admin_identity(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "dynamic-auth.sqlite3"),
        api_key=ADMIN_TOKEN,
    )
    client = TestClient(create_app(settings=settings))
    issued = client.post(
        "/api/v1/admin/tokens",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"owner_id": "bob", "label": "Bob IDE"},
    )
    assert issued.status_code == 201
    token = issued.json()["token"]

    denied = client.post(
        "/api/v1/admin/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"owner_id": "admin", "label": "Escalation"},
    )
    assert denied.status_code == 403

    missing = client.get("/v1/models")
    assert missing.status_code == 401


def test_rate_limit_is_enforced_per_principal_and_audited(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "rate-limit.sqlite3"),
        api_key=ADMIN_TOKEN,
        rate_limit_requests_per_minute=2,
        rate_limit_window_seconds=60,
    )
    client = TestClient(create_app(settings=settings))
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    first = client.get("/v1/models", headers=headers)
    second = client.get("/v1/models", headers=headers)
    third = client.get("/v1/models", headers=headers)

    assert first.status_code == second.status_code == 200
    assert third.status_code == 429
    assert third.headers["retry-after"]
    assert third.headers["x-request-id"]
    assert third.headers["x-ratelimit-remaining"] == "0"

    audit = client.get("/api/v1/admin/audit?limit=20", headers=headers)
    assert audit.status_code == 429  # the same principal is rate-limited on admin routes too
    assert audit.json()["detail"] == "Rate limit exceeded"


def test_rate_limits_are_isolated_between_user_tokens(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "rate-isolation.sqlite3"),
        api_key=ADMIN_TOKEN,
        rate_limit_requests_per_minute=1,
    )
    client = TestClient(create_app(settings=settings))
    admin_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    tokens = []
    for owner in ("alice", "bob"):
        response = client.post(
            "/api/v1/admin/tokens",
            headers=admin_headers,
            json={"owner_id": owner, "label": owner},
        )
        assert response.status_code == 201
        tokens.append(response.json()["token"])

    alice_headers = {"Authorization": f"Bearer {tokens[0]}"}
    bob_headers = {"Authorization": f"Bearer {tokens[1]}"}
    assert client.get("/v1/models", headers=alice_headers).status_code == 200
    assert client.get("/v1/models", headers=alice_headers).status_code == 429
    assert client.get("/v1/models", headers=bob_headers).status_code == 200
