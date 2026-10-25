def init_app(app):
    from sealbook.ingest.views import bp_ingest

    app.register_blueprint(bp_ingest)
