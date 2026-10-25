"""Analyst proposals, directed review and discussion on the evidence ledger."""


def init_app(app):
    from sealbook.collaboration.views import bp

    app.register_blueprint(bp)
    app.navigation.append(
        {
            "title": "Inbox",
            "icon": "ti ti-inbox",
            "to": "/inbox",
            "match": "/inbox",
            "badge": "notifications",
        }
    )
