def init_app(app):
    from sealbook.runs.cli import worker

    app.cli.add_command(worker)
