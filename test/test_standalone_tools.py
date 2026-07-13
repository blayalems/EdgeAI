"""Contract tests for the deterministic standalone builder and checker."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import build_standalone  # noqa: E402
import check_standalone  # noqa: E402


class StandaloneToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "vendor" / "fonts").mkdir(parents=True)
        (self.root / "LICENSE").write_text("MIT License\nDemo project license", encoding="utf-8")
        (self.root / "vendor" / "LICENSES.txt").write_text(
            "MIT License\nDemo third-party notice", encoding="utf-8"
        )
        (self.root / "vendor" / "app.js").write_text("window.vendorReady=true;", encoding="utf-8")
        (self.root / "vendor" / "fonts" / "demo.woff2").write_bytes(b"wOF2-test-font")
        (self.root / "vendor" / "fonts" / "fonts.css").write_text(
            '@font-face{font-family:"Demo";src:url("./demo.woff2") format("woff2")}',
            encoding="utf-8",
        )
        (self.root / "vendor" / "fonts" / "LICENSES.txt").write_text(
            "\n".join(
                (
                    "Copyright 2019 The Manrope Project Authors",
                    "Copyright 2020 The Space Grotesk Project Authors",
                    "Copyright 2026 Google LLC.  All Rights Reserved.",
                    "SIL OPEN FONT LICENSE Version 1.1",
                    "Apache License",
                    "Version 2.0, January 2004",
                )
            ),
            encoding="utf-8",
        )
        (self.root / "support.js").write_text("window.supportReady=true;", encoding="utf-8")
        (self.root / "Ring.dc.html").write_text("<x-dc><div>ring</div></x-dc>", encoding="utf-8")
        (self.root / "index.html").write_text(
            """<!doctype html><html><head>
<link rel="stylesheet" href="./vendor/fonts/fonts.css">
<script src="./vendor/app.js"></script><script src="./support.js"></script>
</head><body><x-dc><div>DEMO</div><dc-ring></dc-ring>
<a href="./LICENSE">Project license</a>
<a href="./vendor/LICENSES.txt">Third-party licenses</a>
<a href="./vendor/fonts/LICENSES.txt">Font licenses</a>
</x-dc></body></html>""",
            encoding="utf-8",
        )
        self.output = self.root / "dist" / "standalone.html"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self) -> bytes:
        build_standalone.build(self.root, self.output, "a" * 40)
        return self.output.read_bytes()

    def test_build_is_deterministic_and_extractable(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        extract = self.root / "extract"
        result = check_standalone.check_artifact(self.output, self.root, extract)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(
            (extract / "index.html").read_bytes(),
            (self.root / "index.html").read_bytes(),
        )
        artifact = first.decode("utf-8")
        self.assertIn("data:font/woff2;base64,", artifact)
        self.assertIn("__BANANAGUARD_EMBEDDED_RESOURCES__", artifact)
        self.assertIn('rel="license"', artifact)
        self.assertIn("bananaguard-third-party-font-licenses", artifact)
        self.assertIn('data-bg-bundle-path="LICENSE"', artifact)
        self.assertIn('data-bg-bundle-path="vendor/LICENSES.txt"', artifact)
        self.assertNotIn('<script src="', artifact)

    def test_uncommitted_runtime_inputs_are_marked_dirty(self) -> None:
        commit = "b" * 40
        with mock.patch.object(
            build_standalone.subprocess,
            "check_output",
            side_effect=[commit + "\n", " M index.html\n"],
        ):
            self.assertEqual(build_standalone.source_commit(self.root), commit + "-dirty")
        self.assertEqual(build_standalone.commit_token(commit + "-dirty"), "b" * 12 + "-dirty")

    def test_same_document_fetch_uses_embedded_index(self) -> None:
        rewritten = "<!doctype html><title>rewritten offline index</title>"
        resources = build_standalone.embedded_fetch_resources(self.root, rewritten)
        self.assertIn("index.html", resources)
        self.assertNotIn("support.js", resources)
        self.assertFalse(any(path.startswith("vendor/") for path in resources))
        self.assertEqual(resources["index.html"]["mime"], "text/html")
        self.assertEqual(
            base64.b64decode(resources["index.html"]["data"]),
            rewritten.encode("utf-8"),
        )

    def test_reviewed_text_hash_is_line_ending_stable(self) -> None:
        self.assertEqual(
            check_standalone.normalized_sha256(b"first\r\nsecond\r"),
            check_standalone.normalized_sha256(b"first\nsecond\n"),
        )

    def test_checker_detects_stale_source(self) -> None:
        self.build()
        (self.root / "support.js").write_text("window.supportReady=false;", encoding="utf-8")
        with self.assertRaisesRegex(check_standalone.CheckError, "stale"):
            check_standalone.check_artifact(self.output, self.root)

    def test_checker_rejects_legacy_actuator_claim(self) -> None:
        index = self.root / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("DEMO", "Solenoid pulsed"), encoding="utf-8")
        self.build()
        with self.assertRaisesRegex(check_standalone.CheckError, "solenoid pulse claim"):
            check_standalone.check_artifact(self.output, self.root)

    def test_checker_rejects_tampered_license_link(self) -> None:
        artifact = self.build().decode("utf-8")
        artifact = artifact.replace(
            "data:text/plain;charset=utf-8;base64,Q29weXJpZ2h0",
            "data:text/plain;charset=utf-8;base64,Rm9yZ2VkICAg",
            1,
        )
        self.output.write_text(artifact, encoding="utf-8")
        with self.assertRaisesRegex(check_standalone.CheckError, "license link differs"):
            check_standalone.check_artifact(self.output, self.root)


if __name__ == "__main__":
    unittest.main()
