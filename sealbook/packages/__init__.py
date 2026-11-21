def init_app(app):
    from sealbook.packages.cli import demo_cert
    from sealbook.packages.views import packages

    app.register_blueprint(packages)
    app.cli.add_command(demo_cert)
    app.navigation.append(
        {
            "title": "Packages",
            "group": "more",
            "icon": "ti ti-package",
            "to": "/packages",
            "match": "/packages",
            "note": "Preview",
        }
    )
