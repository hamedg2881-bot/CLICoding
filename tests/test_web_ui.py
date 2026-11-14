from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database


def test_root_serves_first_party_chat_ui_and_progress_controls(tmp_path):
    database = Database(str(tmp_path / "web-ui.sqlite3"))
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "web-ui.sqlite3")),
        database=database,
    )
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "CDS_AI" in response.text
    assert 'id="stopButton"' in response.text
    assert 'event === "progress"' in response.text
    assert "/api/v1/conversations/" in response.text
    assert "/v1/chat/completions" in response.text
    assert "sessionStorage" in response.text
