def init_app(app):
    from sealbook.watch.views import bp_watch

    app.register_blueprint(bp_watch)
    app.navigation.append(
        {
            "title": "Watching",
            "group": "more",
            "icon": "ti ti-eye",
            "to": "/watch",
            "match": "/watch",
        }
    )
