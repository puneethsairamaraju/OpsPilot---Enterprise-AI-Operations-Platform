def test_health(client):
    assert client.get("/health").json()["status"] == "healthy"


def test_login_rejects_bad_password(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@opspilot.dev", "password": "wrong"},
    )
    assert response.status_code == 401


def test_grounded_query_returns_citation(client, admin_headers):
    response = client.post(
        "/api/query",
        headers=admin_headers,
        json={"question": "What happens during a Severity 1 incident?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] > 0.5
    assert body["citations"]
    assert "incident" in body["answer"].lower()


def test_ingestion_is_searchable(client, admin_headers):
    created = client.post(
        "/api/documents",
        headers=admin_headers,
        json={
            "title": "Travel Policy",
            "content": "International travel requires director approval. Receipts are due within ten days after returning.",
            "source": "manual",
            "classification": "internal",
        },
    )
    assert created.status_code == 201
    result = client.post(
        "/api/query",
        headers=admin_headers,
        json={"question": "Who approves international travel?"},
    )
    assert "director" in result.json()["answer"].lower()


def test_viewer_cannot_ingest(client):
    token = client.post(
        "/api/auth/login",
        json={"email": "viewer@opspilot.dev", "password": "viewer123!"},
    ).json()["access_token"]
    response = client.post(
        "/api/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Blocked document",
            "content": "This valid-length content must not be ingested by a viewer account.",
        },
    )
    assert response.status_code == 403


def test_realtime_connector_upserts_and_deletes(client, admin_headers):
    connection = client.post(
        "/api/connectors",
        headers=admin_headers,
        json={"provider": "slack", "name": "Engineering Slack", "config": {}},
    )
    assert connection.status_code == 201
    created = connection.json()
    event_url = created["webhook_url"]
    event_headers = {"X-Connector-Secret": created["webhook_secret"]}
    event = {
        "event_id": "evt-1",
        "operation": "upsert",
        "external_id": "channel-1:message-1",
        "title": "Slack incident thread",
        "content": "The lunar service is owned by the platform reliability team.",
        "allowed_roles": ["admin"],
        "metadata": {"channel": "incidents"},
    }
    assert client.post(event_url, headers=event_headers, json=event).json()["status"] == "upsert"
    assert client.post(event_url, headers=event_headers, json=event).json()["status"] == "duplicate"
    answer = client.post(
        "/api/query",
        headers=admin_headers,
        json={"question": "Who owns the lunar service?"},
    ).json()
    assert "platform reliability" in answer["answer"].lower()
    deletion = {
        "event_id": "evt-2",
        "operation": "delete",
        "external_id": "channel-1:message-1",
    }
    assert client.post(event_url, headers=event_headers, json=deletion).status_code == 200


def test_text_file_upload_is_searchable(client, admin_headers):
    uploaded = client.post(
        "/api/documents/upload",
        headers=admin_headers,
        files={
            "files": (
                "benefits.txt",
                b"Dental insurance begins after thirty days of employment.",
                "text/plain",
            )
        },
        data={"classification": "internal"},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()[0]["title"] == "benefits"
    answer = client.post(
        "/api/query",
        headers=admin_headers,
        json={"question": "When does dental insurance begin?"},
    ).json()
    assert "thirty days" in answer["answer"].lower()


def test_sample_database_is_idempotent(client, admin_headers):
    first = client.post("/api/demo/load-sample-data", headers=admin_headers)
    second = client.post("/api/demo/load-sample-data", headers=admin_headers)
    assert first.status_code == 201
    assert len(first.json()) == 5
    assert len(second.json()) == 5
