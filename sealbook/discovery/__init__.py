"""Search and media matching over derived evidence, with proposals in the ledger."""


def init_app(app):
    from sealbook.discovery.cli import discover, enrich
    from sealbook.discovery.views import bp

    app.register_blueprint(bp)
    app.cli.add_command(discover)
    app.cli.add_command(enrich)
    app.navigation.append(
        {
            "title": "Discover",
            "icon": "ti ti-robot",
            "to": "/discover",
            "match": "/discover",
        }
    )
