"""site/js/verify-package.js, the browser check on site/verify.html, against the
offline verifier: every package case in test_verify_package runs through both, zipped
as packages.service._zip zips an export, and the two must agree."""

import json
import shutil
import subprocess
import unittest
import zipfile
from pathlib import Path

from sealbook.ledger import chain
from sealbook.ledger.contracts import CHAINED_FIELDS
from tests.test_verify_package import PackageTests

SCRIPT = Path(__file__).parent.parent / "site/js/verify-package.js"
NODE = shutil.which("node")
RUN = """
const V = require(process.argv[1]);
const fs = require("fs");
const [mode, arg] = process.argv.slice(2);
(async () => {
  let out;
  if (mode === "zip") {
    const r = await V.verify(await V.readZip(fs.readFileSync(arg)));
    out = { problems: r.problems, notRun: r.notRun.map((n) => n.key), info: r.info };
  } else if (mode === "canon") {
    out = JSON.parse(arg).map(V.canonOf);
  } else out = V.CHAINED_FIELDS;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(2); });
"""
# Python checks the page leaves to the offline verifier and lists as not run.
NOT_HERE = {
    "TSA signature": "tsa",
    "TSA token content digest": "tsa",
    "TSA signer": "tsa",
    "untrusted": "keys",
}


def node(*args):
    done = subprocess.run(
        [NODE, "-e", RUN, str(SCRIPT), *args], capture_output=True, text=True
    )
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def pack(directory: Path, target: Path, method=zipfile.ZIP_DEFLATED, folder=""):
    with zipfile.ZipFile(target, "w", method) as z:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                z.write(path, folder + path.relative_to(directory).as_posix())
    return target


@unittest.skipUnless(NODE, "the browser check runs under node")
class BrowserCheckTests(PackageTests):
    """Inherits every PackageTests case; run_verifier also runs the page's check."""

    def browser(self, **kwargs):
        target = Path(self.temp.name + ".zip")
        self.addCleanup(target.unlink, missing_ok=True)
        return node("zip", str(pack(self.directory, target, **kwargs)))

    def run_verifier(self, *args, attest=True):
        result = super().run_verifier(*args, attest=attest)
        if any(p.is_symlink() for p in self.directory.rglob("*")):
            return result  # a zip carries no symlinks
        python = {
            line.removeprefix("ERROR ")
            for line in result.stdout.splitlines()
            if line.startswith("ERROR ")
        }
        page = self.browser()
        # The same rows fail; library error texts may differ after the label.
        label = lambda problems: {p.split(": ")[0] for p in problems}  # noqa: E731
        here = {p for p in python if not any(k in p for k in NOT_HERE)}
        if here != python:  # an anchor the page did not fully check leaves its batch
            here = {p for p in here if not p.endswith(": missing anchor")}
        if args:  # pins are flags of the offline verifier; the page applies none
            self.assertLessEqual(label(page["problems"]), label(python), result.stdout)
        else:
            self.assertEqual(label(page["problems"]), label(here), result.stdout)
            self.assertEqual(bool(page["problems"]), bool(here), result.stdout)
        for problem in python - here:
            if key := next((v for k, v in NOT_HERE.items() if k in problem), None):
                self.assertIn(key, page["notRun"], problem)
        return result

    def test_chained_fields_match(self):
        self.assertEqual(
            node("fields"), {k: list(v) for k, v in CHAINED_FIELDS.items()}
        )

    def test_canon_matches(self):
        values = [
            {"text": "محمد", "lang": "ar", "z": [1, -0, None, True]},
            {"\U0001f600": 1, "￿": 2, "a\u0000\u001f\u007f ": '"\\/\b'},
            {"big": 2**70, "nested": {"b": [], "a": {}}},
        ]
        texts = [json.dumps(v, ensure_ascii=True) for v in values]
        self.assertEqual(
            node("canon", json.dumps(texts)), [chain.canon(v).decode() for v in values]
        )

    def test_stored_entries_and_a_wrapping_folder(self):
        self.assert_valid()
        page = self.browser(method=zipfile.ZIP_STORED, folder="export/")
        self.assertEqual(page["problems"], [])

    def test_names_what_it_did_not_check(self):
        self.anchor("tsa")
        self.assert_valid()
        self.assertIn("tsa", self.browser()["notRun"])
        self.anchor("ots")
        self.assert_valid()
        self.assertEqual(self.browser()["notRun"], ["ots", "keys"])


del PackageTests  # run the inherited cases once, here
