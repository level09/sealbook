"""Case list and creation. Analysts and admins only."""

import orjson
from quart import Blueprint, Response, g, request
from quart_security import current_user

from sealbook.cases import service
from sealbook.collaboration.views import guard, no_store
from sealbook.ledger.write import principal_for_user

bp_cases = Blueprint("cases", __name__, url_prefix="/api/cases")
bp_cases.before_request(guard)
bp_cases.after_request(no_store)


def _json(payload, status=200):
    return Response(
        orjson.dumps(payload), status=status, content_type="application/json"
    )


@bp_cases.get("")
async def listing():
    return _json({"items": await service.listing(g.db_session)})


@bp_cases.post("")
async def create():
    item = ((await request.get_json(silent=True)) or {}).get("item") or {}
    db = g.db_session
    who = await principal_for_user(
        db, current_user.id, current_user.name or current_user.email
    )
    try:
        case = await service.create(
            db,
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            who.id,
            confirm=item.get("confirm") is True,
        )
    except (service.Taken, service.Close) as exc:
        reason = "taken" if isinstance(exc, service.Taken) else "close"
        return _json(
            {"reason": reason, "message": str(exc), "match": service.row(exc.match)},
            409,
        )
    except ValueError as exc:
        return _json({"reason": "invalid", "message": str(exc)}, 400)
    await db.commit()
    return _json({"item": service.row(case)}, 201)
