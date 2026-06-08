"""Integration tests for the /api/* routes registered by gateway/http/api_routes.py."""


def _admin(client):
    r = client.post(
        "/auths/signup",
        json={"email": "api@t.com", "name": "A", "password": "pass1234!"},
    )
    return r.json()["token"]  # first signup = admin


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


# ── /api/config ────────────────────────────────────────────────────────────────


def test_get_config_status_200(client):
    r = client.get("/api/config")
    assert r.status_code == 200


def test_get_config_has_required_keys(client):
    r = client.get("/api/config")
    body = r.json()
    for key in ("status", "name", "version", "features"):
        assert key in body, f"missing key: {key}"


def test_get_config_features_is_dict(client):
    r = client.get("/api/config")
    assert isinstance(r.json()["features"], dict)


# ── /api/models ────────────────────────────────────────────────────────────────


def test_get_models_status_200(client):
    token = _admin(client)
    r = client.get("/api/models", headers=_auth(token))
    assert r.status_code == 200


def test_get_models_has_data_list(client):
    token = _admin(client)
    r = client.get("/api/models", headers=_auth(token))
    body = r.json()
    assert "data" in body
    assert isinstance(body["data"], list)


def test_get_models_requires_auth(client):
    r = client.get("/api/models")
    assert r.status_code in (401, 403)


# ── /api/models/base ──────────────────────────────────────────────────────────


def test_get_base_models_status_200(client):
    token = _admin(client)
    r = client.get("/api/models/base", headers=_auth(token))
    assert r.status_code == 200


def test_get_base_models_has_data_list(client):
    token = _admin(client)
    r = client.get("/api/models/base", headers=_auth(token))
    body = r.json()
    assert "data" in body
    assert isinstance(body["data"], list)


def test_get_base_models_requires_auth(client):
    r = client.get("/api/models/base")
    assert r.status_code in (401, 403)


# ── /api/version/updates ──────────────────────────────────────────────────────


def test_get_version_updates_status_200(client):
    r = client.get("/api/version/updates")
    assert r.status_code == 200


def test_get_version_updates_has_current_and_latest(client):
    r = client.get("/api/version/updates")
    body = r.json()
    assert "current" in body
    assert "latest" in body


# ── /api/changelog ────────────────────────────────────────────────────────────


def test_get_changelog_status_200(client):
    r = client.get("/api/changelog")
    assert r.status_code == 200


def test_get_changelog_is_dict(client):
    r = client.get("/api/changelog")
    assert isinstance(r.json(), dict)


# ── /api/config/model/filter ──────────────────────────────────────────────────


def test_get_model_filter_status_200(client):
    token = _admin(client)
    r = client.get("/api/config/model/filter", headers=_auth(token))
    assert r.status_code == 200


def test_get_model_filter_shape(client):
    token = _admin(client)
    r = client.get("/api/config/model/filter", headers=_auth(token))
    body = r.json()
    assert "enabled" in body
    assert body["enabled"] is False
    assert "models" in body
    assert isinstance(body["models"], list)


def test_get_model_filter_requires_auth(client):
    r = client.get("/api/config/model/filter")
    assert r.status_code in (401, 403)


# ── POST /api/tasks/stop/{task_id} ────────────────────────────────────────────


def test_stop_task_status_200(client):
    token = _admin(client)
    r = client.post("/api/tasks/stop/abc", headers=_auth(token))
    assert r.status_code == 200


def test_stop_task_stopped_is_false(client):
    token = _admin(client)
    r = client.post("/api/tasks/stop/abc", headers=_auth(token))
    assert r.json()["stopped"] is False


def test_stop_task_requires_auth(client):
    r = client.post("/api/tasks/stop/abc")
    assert r.status_code in (401, 403)
