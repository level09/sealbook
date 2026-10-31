def init_app(app):
    from sealbook.entities.views import bp_entities

    app.register_blueprint(bp_entities)
    app.navigation.append(
        {
            "title": "Incidents",
            "group": "case",
            "icon": "ti ti-alert-triangle",
            "hue": "is-incident",
            "to": "/incidents",
            "match": "/incidents",
        }
    )
