def init_app(app):
    from sealbook.casemap.views import bp_casemap

    app.register_blueprint(bp_casemap)
    app.navigation.append(
        {
            "title": "Case map",
            "icon": "ti ti-map-2",
            "to": "/case-map",
            "match": "/case-map",
        }
    )
