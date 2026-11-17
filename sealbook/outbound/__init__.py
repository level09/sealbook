def init_app(app):
    from sealbook.outbound.views import bp_outbound

    app.register_blueprint(bp_outbound)
    app.navigation.append(
        {
            "title": "What left this server",
            "group": "more",
            "icon": "ti ti-world-upload",
            "to": "/outbound",
            "match": "/outbound",
        }
    )
