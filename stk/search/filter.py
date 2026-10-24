"""Shared filter validation, canonical URL encoding and table-independent SQL."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import date

from sqlalchemy import and_, not_, or_, true
from sqlalchemy.sql.elements import ColumnElement

from stk.ledger.chain import canon
from stk.ledger.contracts import PrincipalKind, Prop, Tier

_FIELDS = {
    "investigation",
    "time",
    "place",
    "entity",
    "tier",
    "prop",
    "doc_type",
    "origin",
    "author_kind",
}
_ENUMS = {"tier": Tier, "prop": Prop, "author_kind": PrincipalKind}


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("date must be a string or null")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise ValueError(f"invalid ISO date: {value!r}") from None


def _value(field, value):
    if not isinstance(value, str):
        raise ValueError(f"{field} value must be a string")
    if field in _ENUMS and value not in {e.value for e in _ENUMS[field]}:
        raise ValueError(f"invalid {field}: {value!r}")
    if field == "entity":
        kind, sep, identifier = value.partition(":")
        try:
            if not sep or not kind or not re.fullmatch(r"[a-z][a-z0-9_]*", kind):
                raise ValueError
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError
        except ValueError:
            raise ValueError(f"invalid entity: {value!r}") from None


def validate(ast):
    """Return the AST unchanged. Root depth is one; lists must be non-empty."""
    leaves = 0

    def visit(node, depth):
        nonlocal leaves
        if depth > 6:
            raise ValueError("filter depth exceeds 6")
        if not isinstance(node, dict):
            raise ValueError("node must be an object")
        logical = {"and", "or", "not"} & node.keys()
        if logical:
            key = next(iter(logical))
            if set(node) != {key}:
                raise ValueError("unknown keys in logical node")
            if key == "not":
                visit(node[key], depth + 1)
            else:
                children = node[key]
                if not isinstance(children, list):
                    raise ValueError(f"{key} must be a list")
                if not children:
                    raise ValueError(f"{key} must not be empty")
                for child in children:
                    visit(child, depth + 1)
            return
        if set(node) - {"field", "op", "value"}:
            raise ValueError("unknown keys in leaf")
        if set(node) != {"field", "op", "value"}:
            raise ValueError("expected a logical node or leaf")
        leaves += 1
        if leaves > 50:
            raise ValueError("filter has more than 50 leaves")
        field, op, value = node["field"], node["op"], node["value"]
        if not isinstance(field, str) or field not in _FIELDS:
            raise ValueError(f"unknown field: {field!r}")
        ops = (
            ("between",)
            if field == "time"
            else (("eq",) if field == "author_kind" else ("eq", "in"))
        )
        if op not in ops:
            raise ValueError(f"unknown op for {field}: {op!r}")
        if field == "time":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("between requires two bounds")
            start, end = map(_date, value)
            if start is not None and end is not None and start > end:
                raise ValueError("time bounds are reversed")
        elif op == "in":
            if not isinstance(value, list):
                raise ValueError("in value must be a list")
            if not value:
                raise ValueError("in-list must not be empty")
            for item in value:
                _value(field, item)
        else:
            _value(field, value)

    visit(ast, 1)
    return ast


def compile(ast, columns: dict[str, ColumnElement]) -> ColumnElement:
    """Compile inclusive range overlap, treating NULL endpoints as open."""
    validate(ast)

    def build(node):
        if "and" in node:
            return and_(*(build(child) for child in node["and"]))
        if "or" in node:
            return or_(*(build(child) for child in node["or"]))
        if "not" in node:
            return not_(build(node["not"]))
        field, op, value = node["field"], node["op"], node["value"]
        if field == "time":
            start, end = map(_date, value)
            lower, upper = columns["time_from"], columns["time_to"]
            clauses = []
            if start is not None:
                clauses.append(or_(upper.is_(None), upper >= start))
            if end is not None:
                clauses.append(or_(lower.is_(None), lower <= end))
            return and_(*clauses) if clauses else true()
        col = columns[field]
        return col == value if op == "eq" else col.in_(value)

    return build(ast)


def encode(ast) -> str:
    return base64.urlsafe_b64encode(canon(validate(ast))).decode("ascii").rstrip("=")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def decode(s):
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        raise ValueError("invalid base64url filter")
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_", validate=True)
        ast = json.loads(raw, object_pairs_hook=_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid encoded filter") from None
    return validate(ast)


def differs(a, b) -> bool:
    return canon(validate(a)) != canon(validate(b))
