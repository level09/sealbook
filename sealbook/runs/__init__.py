def init_app(app):
    from sealbook.runs.cli import runner

    app.cli.add_command(runner)
