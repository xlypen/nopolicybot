def test_smoke_environment():
    import admin_app
    import services.graph_api
    import db.engine

    assert admin_app.app is not None
