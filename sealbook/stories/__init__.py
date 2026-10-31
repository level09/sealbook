def init_app(app):
    from sealbook.stories.views import bp_stories

    app.register_blueprint(bp_stories)
    app.navigation.append(
        {
            "title": "Stories",
            "group": "case",
            "icon": "ti ti-timeline",
            "hue": "is-story",
            "to": "/stories",
            "match": "/stories",
        }
    )
