"""Sources arriving in an act: each through intake, then the worker's local tools
(geotag, thumbnail, fingerprint, index) on it before the next one, as a worker
keeping up would. A post a file came with is its own item beside it. A source with
a fixed `transcript` is read by the app's own OCR or speech writer from that text;
a source marked `read` waits for the `read` step, which replays the recorded
answers of the models that read it."""

from __future__ import annotations

from pathlib import Path

from sealbook.monitor.seed_full import intake
from sealbook.monitor.seed_study import MIME, file_of, settle, stand_in, transcribe


async def sources(st, keys: list[str]) -> None:
    spec, s = st.spec, st.s
    for key in keys:
        src = spec["sources"][key]
        origin = src.get("origin") or st.channel
        context = {"title": src["title"], "fictional": True}
        if st.batch and key in spec["batch"]:
            context["batch"] = st.batch
        if post := src.get("post"):
            await intake(
                s,
                f"{key}_post",
                post["text"].encode(),
                "text/plain",
                origin,
                st.collector,
                post.get("doc_type", "social_post"),
                src["sensitivity"],
                context | {"title": post["title"], "file": f"{key}_post.txt"},
            )
            st.text_of[key] = f"{key}_post"
            await settle(s, f"{key}_post")
        if src.get("file"):
            file = file_of(st.root, src["file"])
            data, mime = file.read_bytes(), MIME[file.suffix.lower()]
        elif src.get("text") is not None:
            data, mime, file = src["text"].encode(), "text/plain", Path(f"{key}.txt")
        else:
            text, spans = stand_in(spec, key, st.claims[key])
            data, mime, file = text.encode(), "text/plain", Path(f"{key}.txt")
            st.pins |= spans
        await intake(
            s,
            key,
            data,
            mime,
            origin,
            st.collector,
            src["doc_type"],
            src["sensitivity"],
            context | {"file": file.name},
        )
        st.text_of.setdefault(key, key)
        if src.get("transcript") and not st.read(key):
            said = file_of(st.root, src["transcript"])
            st.text_of[key] = await transcribe(
                s, key, mime, said, f"acceptance/cases/{st.root.name}/files/{said.name}"
            )
        await settle(s, key)
        st.arrived.append(key)
        await st.session.commit()
