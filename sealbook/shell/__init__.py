"""The shell every Sealbook page shares: versioned assets, the rail, the account
pages, the landing redirect, the passkey return path and the missing-file 404."""


def init_app(app):
    from urllib.parse import urlsplit

    from quart import flash, redirect, render_template, request
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

    FORM_TYPES = {"application/x-www-form-urlencoded", "multipart/form-data"}

    def _is_api_request():
        return (
            request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
        )

    @app.errorhandler(400)
    async def bad_request(error):
        # A form sent with an old token (a page left open while the session changed)
        # is not the person's mistake: hand the page back with a fresh token. The
        # wording is stk.security's; tests/test_security_compatibility.py pins it.
        if _is_api_request():
            return {"message": error.description}, 400
        # Only a form a person submitted: scripts posting JSON keep their plain 400.
        form = request.mimetype in FORM_TYPES
        if not form or error.description != "Invalid CSRF token":
            return await render_template(["400.html", "500.html"]), 400
        await flash(
            "That page had expired. Nothing was sent; please try again.", "warning"
        )
        # Back to the page the form was on, never off this site.
        came = urlsplit(request.referrer or "")
        back = came.path if came.netloc == request.host else request.path
        return redirect("/" + back.lstrip("/"), 303)

    @app.errorhandler(MissingBytes)
    async def no_file(error):
        # Synthetic filler records and lost files: a plain 404, never a 500.
        if _is_api_request():
            return {
                "message": "No file is stored for this source",
                "reason": "no_file",
            }, 404
        return await render_template(["404.html"]), 404
