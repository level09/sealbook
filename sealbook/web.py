"""What the app's signed-in pages and APIs share: analysts and admins only, nothing
cached, JSON out. `protect(bp)` attaches the first two to a blueprint."""

import orjson
from quart import Response, abort
from quart_security import auth_required, current_user

# ponytail: any analyst or admin sees every investigation; per-investigation access
# groups replace this check once the ledger has an access model.
ROLES = ("analyst", "admin")


@auth_required("session")
async def guard():
    if not any(current_user.has_role(r) for r in ROLES):
        abort(403)


async def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def protect(*blueprints):
    for bp in blueprints:
        bp.before_request(guard)
        bp.after_request(no_store)


def json_response(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )
