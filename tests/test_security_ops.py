from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.security import SecurityStore


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

    audit = client.get(
        "/api/v1/admin/audit?limit=50",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert audit.status_code == 200
    assert any(
        event["route"] == "/api/v1/admin/tokens"
        and event["action"] == "http.request"
        for event in audit.json()["events"]
    )
    assert token["token"] not in audit.text


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

    audit = SecurityStore(settings.database_path).list_audit(limit=20)
    assert any(
        event["action"] == "request.rate_limited"
        and event["status_code"] == 429
        and event["route"] == "/v1/models"
        for event in audit
    )


def test_rate_limits_are_isolated_between_user_tokens(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "rate-isolation.sqlite3"),
        api_key=ADMIN_TOKEN,
        rate_limit_requests_per_minute=1,
    )
    client = TestClient(create_app(settings=settings))
    store = SecurityStore(settings.database_path)
    tokens = [
        store.issue_token(owner, owner)["token"]
        for owner in ("alice", "bob")
    ]

    alice_headers = {"Authorization": f"Bearer {tokens[0]}"}
    bob_headers = {"Authorization": f"Bearer {tokens[1]}"}
    assert client.get("/v1/models", headers=alice_headers).status_code == 200
    assert client.get("/v1/models", headers=alice_headers).status_code == 429
    assert client.get("/v1/models", headers=bob_headers).status_code == 200
