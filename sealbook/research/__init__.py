def init_app(app):
    from sealbook.research.views import bp_research

    app.register_blueprint(bp_research)
