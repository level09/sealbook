def init_app(app):
    from sealbook.review.views import review

    app.register_blueprint(review)
    app.navigation.append(
        {
            "title": "Review",
            "icon": "ti ti-checklist",
            "hue": "tier-accepted",
            "to": "/review",
            "match": "/review",
            "badge": "review",
        }
    )
