def init_app(app):
    from sealbook.sheets.views import bp

    app.register_blueprint(bp)
