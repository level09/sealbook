def init_app(app):
    from sealbook.monitor.seed import demo_seed
    from sealbook.monitor.views import monitor

    app.register_blueprint(monitor)
    app.cli.add_command(demo_seed)
