def init_app(app):
    from sealbook.bayanat.views import bp

    app.register_blueprint(bp)
