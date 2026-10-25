def init_app(app):
    from sealbook.entities.views import bp_entities

    app.register_blueprint(bp_entities)
