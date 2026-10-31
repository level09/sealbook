"""The shell every Sealbook page shares: versioned assets, the rail, the account
pages, the landing redirect, the passkey return path and the missing-file 404."""


def init_app(app):
    from quart import redirect, render_template, request
    from quart_security import current_user

    from sealbook.assets import register_assets
    from sealbook.blobs import MissingBytes
    from sealbook.shell.views import bp_account, register_passkey_return

    register_assets(app)
    app.register_blueprint(bp_account)
    register_passkey_return(app)

    async def index():
        # No marketing page: analysts land on the Desk, everyone else signs in.
        return redirect("/todo" if current_user.is_authenticated else "/login")

    # ponytail: swaps stk's "/" view in place, as stk has no landing-page hook;
    # use that hook once stk ships one.
    app.view_functions["public.index"] = index

    def _is_api_request():
        return (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
        )

    @app.errorhandler(MissingBytes)
    async def no_file(error):
        # Synthetic filler records and lost files: a plain 404, never a 500.
        if _is_api_request():
            return {
                "message": "No file is stored for this source",
                "reason": "no_file",
            }, 404
        return await render_template(["404.html"]), 404
