"""`package`: the case leaves as a sealed package, through the app's own package
service. The case analyst writes a brief (`title`, `audience`) whose sentences
(`sentences: [{"text", "cite": [claim keys]}]`) cite claims a person accepted; the
brief opens with the case's signed conclusion and carries what it rests on. Then:

1. Anchoring: `stk anchor` once (`sealbook.anchor.service.run_once`), the real
   one. It closes a Merkle batch over the chain and sends its root, a hash and
   nothing else, to the OpenTimestamps calendars and the timestamp authority
   (OTS_CALENDARS, TSA_URL). Offline, or when a witness refuses, the step stops
   here and says the build waits for anchoring; nothing is faked.
2. Build, signed with C2PA_SIGN_CERT and C2PA_SIGN_KEY (`stk demo-cert` makes a
   demo pair for development); unset, the step stops and says so.
3. The analyst and the peer reviewer each attest the manifest hash with their
   passkey, and the analyst finalizes.
4. The export is saved at `save` (relative to the project root) and checked as a
   recipient would, with no database: `sealbook.ledger.verify_package` and
   `c2patool` on the signed cover.

`out["package"]` holds what happened: the statements disclosed and each check's
result, or what the build waits for."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import httpx2
import stk.extensions as ext
from stk.user.models import User

from sealbook.anchor import service as anchoring
from sealbook.ledger.contracts import Refused
from sealbook.ledger.verify_package import verify_package
from sealbook.monitor.study import step
from sealbook.packages import service
from sealbook.packages.models import Brief
from sealbook.settings import Config

ROOT = Path(Config.PROJECT_ROOT)


def credential(seeder, digest: str) -> dict:
    """The browser's assertion JSON for the seeder's demo passkey over `digest`."""
    sig = seeder.assertion(digest)
    return {
        "id": sig["credential_id"],
        "rawId": sig["credential_id"],
        "type": "public-key",
        "response": {
            "authenticatorData": sig["authenticator_data"],
            "clientDataJSON": sig["client_data_json"],
            "signature": sig["signature"],
        },
        "clientExtensionResults": {},
    }


async def anchor() -> list[str]:
    """`stk anchor`, once: the real witnesses, over the network."""
    async with httpx2.AsyncClient(timeout=30) as client:
        _, errors = await anchoring.run_once(ext.async_session_factory, client)
    return errors


@step("package")
async def package(st, act, args) -> None:
    db = st.session
    user = await db.get(User, st.analyst.user_id)
    brief = Brief(title=args["title"], audience=args["audience"], author_id=user.id)
    db.add(brief)
    await db.flush()
    for sentence in args["sentences"]:
        missing = [k for k in sentence["cite"] if k not in st.written]
        if missing:
            raise ValueError(f"the brief cites {missing}, which no source has given")
        ids = [str(st.written[k].id) for k in sentence["cite"]]
        await service.save_sentence(db, brief, user, sentence["text"], ids)
    await db.commit()
    await db.refresh(brief)
    got = st.out["package"] = {
        "brief": str(brief.id),
        "sentences": len(brief.sentences),
    }

    config = {
        "BLOB_DIR": Config.BLOB_DIR,
        "C2PA_SIGN_CERT": st.app.config.get("C2PA_SIGN_CERT"),
        "C2PA_SIGN_KEY": st.app.config.get("C2PA_SIGN_KEY"),
    }
    if not (config["C2PA_SIGN_CERT"] and config["C2PA_SIGN_KEY"]):
        got["waiting"] = "no signing certificate (stk demo-cert makes a demo one)"
        return
    if errors := await anchor():
        got["anchor errors"] = errors
    try:
        built = await service.build(db, brief, user, config)
    except Refused as exc:
        got["waiting"] = exc.reason
        return
    await db.commit()

    # Two people attest the manifest hash with their passkeys, as the page does.
    seeder, reviewer = st.others["reviewer"]
    for s, who in ((st.s, st.analyst), (seeder, reviewer)):
        person = await db.get(User, who.user_id)
        with (
            mock.patch.dict(
                st.app.config,
                {
                    "SECURITY_WAN_RP_ID": "localhost",
                    "SECURITY_WAN_EXPECTED_ORIGIN": "http://localhost",
                },
            ),
        ):
            async with st.app.test_request_context("/", headers={"Host": "localhost"}):
                await service.add_attestation(
                    db, person, built, credential(s, built.manifest_sha256)
                )
        await db.commit()
    await service.finalize(db, user, built, Config.BLOB_DIR)
    body = await service.export(db, user, built, Config.BLOB_DIR)
    await db.commit()

    save = ROOT / args["save"]
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_bytes(body)
    got |= {
        "id": str(built.id),
        "manifest": built.manifest_sha256,
        "statements": built.statements,
        "saved": args["save"],
    }
    got |= await check(body)


async def check(body: bytes) -> dict:
    """What a recipient's offline checks say: the verifier's OK and ERROR lines,
    and c2patool's validation state of the signed cover."""
    tmp = Path(tempfile.mkdtemp(prefix="package-check-"))
    try:
        zipfile.ZipFile(io.BytesIO(body)).extractall(tmp)
        problems, ok = verify_package(tmp)
        out = {"checks passed": len(ok), "problems": problems}
        if shutil.which("c2patool"):
            proc = await asyncio.create_subprocess_exec(
                "c2patool",
                str(tmp / "package.svg"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            raw, _ = await proc.communicate()
            report = json.loads(raw or b"{}")
            out["c2pa"] = report.get("validation_state", "unreadable")
            # A demo signer is on no trust list; c2patool says so beside Valid.
            out["c2pa signer trusted"] = b"signingCredential.untrusted" not in raw
        return out
    finally:
        shutil.rmtree(tmp)
