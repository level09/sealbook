def init_app(app):
    from sealbook.thumbs.views import bp

    app.register_blueprint(bp)
