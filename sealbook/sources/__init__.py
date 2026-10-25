def init_app(app):
    from sealbook.sources.views import bp

    app.register_blueprint(bp)
    app.navigation.append(
        {
            "title": "Sources",
            "icon": "ti ti-files",
            "to": "/sources",
            "match": "/sources",
        }
    )
