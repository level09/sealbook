def init_app(app):
    from sealbook.desk.views import bp

    app.register_blueprint(bp)
    app.navigation.append(
        {
            "title": "Desk",
            "icon": "ti ti-layout-dashboard",
            "to": "/desk",
            "match": "/desk",
        }
    )
