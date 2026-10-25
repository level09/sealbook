def init_app(app):
    from sealbook.stories.views import bp_stories

    app.register_blueprint(bp_stories)
    app.navigation.append(
        {
            "title": "Stories",
            "icon": "ti ti-timeline",
            "to": "/stories",
            "match": "/stories",
        }
    )
