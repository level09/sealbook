def init_app(app):
    from sealbook.anchor.cli import anchor, verify_anchors_cmd

    app.cli.add_command(anchor)
    app.cli.add_command(verify_anchors_cmd)
