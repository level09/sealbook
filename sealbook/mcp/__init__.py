def init_app(app):
    from sealbook.mcp.views import bp_mcp

    app.register_blueprint(bp_mcp)
