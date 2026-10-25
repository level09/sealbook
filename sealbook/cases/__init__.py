def init_app(app):
    from sealbook.cases.views import bp_cases

    app.register_blueprint(bp_cases)
