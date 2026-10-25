"""Analyst proposals, directed review and discussion on the evidence ledger."""


def init_app(app):
    from sealbook.collaboration.views import bp

    app.register_blueprint(bp)
    app.navigation.append(
        {
            "title": "Inbox",
            "icon": "ti ti-inbox",
            "hue": "state-new",
            "to": "/inbox",
            "match": "/inbox",
            "badge": "notifications",
        }
    )
