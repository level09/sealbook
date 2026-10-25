def init_app(app):
    from sealbook.guide.views import bp_guide

    app.register_blueprint(bp_guide)
    app.navigation.append(
        {"title": "Guide", "icon": "ti ti-book", "to": "/guide", "match": "/guide"}
    )
