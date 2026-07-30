from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_display_renders_primary_dashboard_values() -> None:
    response = client.get("/display")

    assert response.status_code == 200
    for label in ("現在の電力", "1.24", "買電", "今日の買電量", "今日の売電量", "温度", "湿度", "CO2", "PM2.5", "空気環境", "最終更新", "normal"):
        assert label in response.text


def test_static_files_are_available() -> None:
    assert client.get("/static/display.css").status_code == 200
    assert client.get("/static/display.js").status_code == 200
