def init_app(app):
    from sealbook.packages.views import packages

    app.register_blueprint(packages)
    app.navigation.append(
        {
            "title": "Packages",
            "icon": "ti ti-package",
            "to": "/packages",
            "match": "/packages",
        }
    )
