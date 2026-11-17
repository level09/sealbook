def init_app(app):
    from sealbook.record.views import bp_record

    app.register_blueprint(bp_record)
