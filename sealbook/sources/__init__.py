def init_app(app):
    from sealbook.sources.views import bp

    app.register_blueprint(bp)
    app.navigation.append(
        {
            "title": "Sources",
            "group": "case",
            "icon": "ti ti-files",
            "hue": "is-source",
            "to": "/sources",
            "match": "/sources",
        }
    )
