"""Planted claims: what an extraction run would write, pinned where the case file
says, for the sources that have arrived and that the app does not read itself, and
what the post a read file came with reports.
`accept` has the case analyst accept the claims planted as accepted, or the claim
keys an act names."""

from __future__ import annotations

from sealbook.ledger.contracts import EvidenceKind as E
from sealbook.monitor.seed_study import person, value_of
from sealbook.monitor.study import step


def first_rows(st) -> dict:
    """A sheet's rows are separate mentions, as the app's sheet intake writes them:
    a record's first row is the record, each later row its own subject, and the
    desk that kept the log has already joined them."""
    out = {}
    for c in st.spec["claims"]:
        if (row := (c.get("pin") or {}).get("row")) and c["record"] in st.records:
            out[c["record"]] = min(out.get(c["record"], row), row)
    return out


@step("claims")
async def claims(st, act, args) -> None:
    s, inv = st.s, st.inv
    first = first_rows(st)

    def subject_of(c: dict, doc: str) -> str:
        if c["record"] not in st.records:
            return f"blob:{s.blobs[doc]}"
        row = (c.get("pin") or {}).get("row")
        if row is None or row == first[c["record"]]:
            return person(inv, c["record"])
        return person(inv, f"{c['record']}/row {row}")

    for key in st.arrived:
        mine = [c for c in st.claims[key] if c["key"] not in st.written]
        doc = st.text_of[key]
        if st.read(key):
            # The app reads the file, not the post it came with: what the post
            # reports is planted from the post's own words.
            post = st.spec["sources"][key].get("post")
            mine = [c for c in mine if post and c["record"] not in st.records]
            doc = f"{key}_post"
        if not mine:
            continue
        run = await s.run(
            st.extractor,
            "extract",
            [doc],
            **PLANTED,
            inputs={"answer": "case file", "demo": "planted from the case file"},
        )
        for c in mine:
            pin = c.get("pin") or st.pins.get(c["key"])
            needle, after, at, on = None, "", None, doc
            if pin is None:
                needle = c["value"]
            elif "text" in pin:
                needle, after = pin["text"], pin.get("after", "")
            else:
                at, on = pin, key  # a cell or a box sits in the file itself
            subject = subject_of(c, doc)
            st.written[c["key"]] = await s.say(
                run,
                on,
                subject,
                c["prop"],
                value_of(c),
                needle,
                after,
                valid=[c["from"], c["to"] or c["from"]] if c["from"] else None,
                evidence=[E.PRINTED_TEXT],
                at=at,
            )
            if c["record"] in st.records:
                st.subjects.setdefault(c["record"], person(inv, c["record"]))
            if subject.startswith("person:") and subject != person(inv, c["record"]):
                st.rows.setdefault((c["record"], subject), st.written[c["key"]])
        await s.done(run, len(mine))
        await st.session.commit()


PLANTED = {
    "model_requested": "planted-from-case.json",
    "model_served": "planted-from-case.json",
    "provider_served": "the case file",
    "endpoint": "case-file",
}


@step("planted")
async def planted(st, act, args) -> None:
    """The fallback for a source the app read: the planted claims `claims` names,
    written because extraction did not propose them (`why`), by their own run whose
    provenance says so ("planted-from-case.json", no model), about the person the
    app wrote for the record, quoting the text the app read. Never silent: the act
    names the claims and the reason, and `out["planted"]` counts them."""
    s, by_key = st.s, {c["key"]: c for c in st.spec["claims"]}
    agent = await s.principal("planted from the case file")
    for key in args["claims"]:
        if key in st.written:
            continue  # the app read it after all
        c = by_key[key]
        source = st.records[c["record"]]["source"]
        doc = st.text_of[source]
        run = await s.run(agent, "extract", [doc], **PLANTED)
        run.inputs = {
            **run.inputs,
            "demo": f"planted from the case file: {args['why']}",
            "answer": "case file",
        }
        pin = c.get("pin") or {}
        after = pin.get("after", "")
        if after not in s.texts[doc]:
            after = ""  # OCR broke the line inside the words the pin follows
        st.written[key] = await s.say(
            run,
            doc,
            st.subjects.get(c["record"]) or person(st.inv, c["record"]),
            c["prop"],
            value_of(c),
            pin.get("text") or c["value"],
            after,
            valid=[c["from"], c["to"] or c["from"]] if c["from"] else None,
            evidence=[E.PRINTED_TEXT],
        )
        await s.done(run, 1)
        st.out["planted"] = st.out.get("planted", 0) + 1


@step("accept")
async def accept(st, act, args) -> None:
    """The case analyst accepts, with a signature, the claims `args["claims"]`
    names, or else every arrived claim planted as accepted."""
    keys = args.get("claims") or [
        c["key"] for c in st.spec["claims"] if c["tier"] == "accepted"
    ]
    reason = args.get("why") or "planted as accepted in the case study"
    for key in keys:
        found = st.written.get(key)
        if found is None or found in st.accepted:
            continue
        await st.s.review(st.analyst, found, "accept", reason)
        st.accepted.append(found)
