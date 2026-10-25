def init_app(app):
    from sealbook.search.views import bp

    app.register_blueprint(bp)
