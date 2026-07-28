import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_opspilot.db"
os.environ["JWT_SECRET"] = "test-secret-with-at-least-thirty-two-bytes"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)
    engine.dispose()
    Path("test_opspilot.db").unlink(missing_ok=True)


@pytest.fixture()
def admin_headers(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@opspilot.dev", "password": "admin123!"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
