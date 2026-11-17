def init_app(app):
    from sealbook.cases.views import bp_case_pages, bp_cases

    app.register_blueprint(bp_cases)
    app.register_blueprint(bp_case_pages)
    # A case's first tab; it needs one case, so the layout shows it only then.
    app.navigation.append(
        {
            "title": "Overview",
            "group": "case",
            "icon": "ti ti-layout-list",
            "to": "/cases/overview",
            "match": "/cases/overview",
            "badge": "overview",
        }
    )
