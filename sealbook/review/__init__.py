def init_app(app):
    from sealbook.review.views import review

    app.register_blueprint(review)
    app.navigation.append(
        {
            "title": "To do",
            "icon": "ti ti-checklist",
            "hue": "tier-accepted",
            "to": "/todo",
            "match": "/todo",
            "badge": "review",
        }
    )
