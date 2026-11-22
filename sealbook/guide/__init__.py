def init_app(app):
    from sealbook.guide.film import bp_film
    from sealbook.guide.views import bp_guide

    app.register_blueprint(bp_guide)
    app.register_blueprint(bp_film)
    app.navigation.append(
        {
            "title": "Guide",
            "group": "more",
            "icon": "ti ti-book",
            "to": "/guide",
            "match": "/guide",
        }
    )
