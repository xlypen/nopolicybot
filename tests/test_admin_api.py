import admin_app


def test_api_restart_status_works():
    app = admin_app.app
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.get("/api/restart-status")
    assert r.status_code == 200
    data = r.get_json()
    assert "requested" in data
