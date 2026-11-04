"""Tests for application setup and operational health endpoints."""

from pathlib import Path

from fingpt import create_app


def test_liveness_is_available_without_database_configuration() -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": None})

    response = app.test_client().get("/health/live")

    assert response.status_code == 200
    assert response.json == {"status": "alive"}


def test_static_web_app_api_proxy_can_reach_public_health_probe() -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": None})

    response = app.test_client().get("/api/health/live")

    assert response.status_code == 200
    assert response.json == {"status": "alive"}


def test_readiness_reports_missing_database_configuration() -> None:
    app = create_app({"TESTING": True, "DATABASE_URL": None})

    response = app.test_client().get("/health/ready")

    assert response.status_code == 503
    assert response.json == {"status": "not_ready", "reason": "database_not_configured"}


def test_serves_spa_routes_and_built_assets_from_configured_directory(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>FinGPT app</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": None,
            "WEB_DIRECTORY": str(tmp_path),
        }
    )

    client = app.test_client()

    assert client.get("/").data == b"<html>FinGPT app</html>"
    assert client.get("/history").data == b"<html>FinGPT app</html>"
    assert client.get("/assets/app.js").data == b"console.log('app')"


def test_api_paths_never_fall_through_to_the_spa(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>FinGPT app</html>", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": None,
            "WEB_DIRECTORY": str(tmp_path),
        }
    )

    response = app.test_client().get("/api/not-found")

    assert response.status_code == 503
    assert response.json is not None
    assert response.json["error"]["code"] == "service_unavailable"
