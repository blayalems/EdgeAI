#!/usr/bin/env python3
"""Validate or extract a BananaGuard deterministic standalone bundle."""

from __future__ import annotations

import argparse
import base64
import binascii
import html.parser
import json
from pathlib import Path, PurePosixPath
import re
import sys

import build_standalone


MANIFEST_RE = re.compile(
    r"<script\b(?=[^>]*\bid=['\"]bananaguard-build-manifest['\"])[^>]*>"
    r"(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
FONT_LICENSE_RE = re.compile(
    r"<script\b(?=[^>]*\bid=['\"]bananaguard-third-party-font-licenses['\"])[^>]*>"
    r"(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
FONT_LICENSE_LINK_RE = re.compile(
    r"<link\b(?=[^>]*\brel=['\"]license['\"])(?=[^>]*\bdata-bg-bundle-path=['\"]"
    + re.escape(build_standalone.FONT_LICENSE_REL)
    + r"['\"])(?=[^>]*\bhref=['\"](?P<href>data:text/plain;charset=utf-8;base64,[^'\"]+)['\"])[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
META_RE_TEMPLATE = r"<meta\b(?=[^>]*\bname=['\"]{name}['\"])(?=[^>]*\bcontent=['\"]([^'\"]*)['\"])[^>]*>"
LEGACY_PATTERNS = (
    ("LIVE-SIM label", re.compile(r"\bLIVE-SIM\b", re.IGNORECASE)),
    ("plain LIVE label", re.compile(r"(?:>\s*LIVE\s*<|['\"]LIVE['\"]\s*[,;}])", re.IGNORECASE)),
    ("LIVE NOW label", re.compile(r"\bLIVE\s+NOW\b", re.IGNORECASE)),
    ("solenoid pulse claim", re.compile(r"\bSolenoid\s+pulsed\b", re.IGNORECASE)),
    ("manual test spray control", re.compile(r"\bTest\s+spray\s+now\b|\bManually\s+pulse\s+the\s+solenoid\b", re.IGNORECASE)),
    ("unverified monsoon autonomy claim", re.compile(r"Verified\s+continuously\s+under\s+monsoon", re.IGNORECASE)),
    ("automatic rain-window claim", re.compile(r"detection\s+window\s+auto-extends", re.IGNORECASE)),
    ("automatic capture-cadence claim", re.compile(r"capture\s+interval\s+auto-densifies", re.IGNORECASE)),
)
EXPECTED_FONT_SHA256 = {
    "vendor/fonts/manrope-latin.woff2": "a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972",
    "vendor/fonts/space-grotesk-latin.woff2": "0640890476fc1198ab4de571fb658de443c4d85b66466ec09534a8737ab1ce9d",
    "vendor/fonts/material-symbols-rounded.woff2": "efc246c05b10dd686c0f8a18036ef74a2a831423b8af62160c89c5f320cea6e2",
}
# Hash after CRLF/CR normalization. This pins the exact notices and complete
# OFL 1.1 + Apache 2.0 texts while remaining stable across Git checkouts.
EXPECTED_FONT_LICENSE_SHA256 = "56ba157a3ad1be9669ea0a126c23fc999804ba6cc239c8bec3d1318a00863b8e"
EXPECTED_JAVASCRIPT_SHA256 = {
    "vendor/react.production.min.js": "d72610e728466bf70f27ecc9a1a14580fd8f1e75b977aa0612146cab0e80a3fe",
    "vendor/react-dom.production.min.js": "13b1c0705b9fa93346936ed98bd4a858fea4cdacdb09e564f897c5ba6bd0943a",
}
EXPECTED_VENDOR_LICENSE_SHA256 = "0fb7854dc6677ffa6d7cdbcdb1d16fc1e8f60fce7ef6932c3e63d183520a4e89"
EXPECTED_PROJECT_LICENSE_SHA256 = "8f06502fa2aa930fd1f4b4974838b2b00d5a5f2536a6e254fec4dabd751f67b7"


class CheckError(RuntimeError):
    """Raised when an artifact is stale, incomplete, or misleading."""


class RuntimeDependencyParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()
        if tag == "script" and values.get("src"):
            self.errors.append(f"script src remains: {values['src']}")
        if tag == "link" and "stylesheet" in values.get("rel", "").lower().split():
            self.errors.append(f"stylesheet link remains: {values.get('href', '')}")
        if tag in {"img", "source", "video", "audio"}:
            src = values.get("src", "")
            if src and not src.startswith(("data:", "blob:", "#")):
                self.errors.append(f"non-embedded {tag} src remains: {src}")
            if values.get("srcset"):
                self.errors.append(f"non-embedded {tag} srcset remains")


class LicenseReferenceParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: dict[str, list[str]] = {}
        self.anchor_paths: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in {"a", "link"}:
            return
        values = {name.lower(): value or "" for name, value in attrs}
        path = values.get("data-bg-bundle-path")
        href = values.get("href")
        if path and href:
            self.references.setdefault(path, []).append(href)
            if tag.lower() == "a":
                self.anchor_paths.add(path)


def read_manifest(artifact_text: str) -> dict[str, object]:
    match = MANIFEST_RE.search(artifact_text)
    if not match:
        raise CheckError("embedded build manifest is missing")
    try:
        manifest = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"embedded build manifest is invalid JSON: {exc}") from exc
    if manifest.get("schema_version") != build_standalone.SCHEMA_VERSION:
        raise CheckError(f"unsupported manifest schema: {manifest.get('schema_version')!r}")
    return manifest


def decode_entries(manifest: dict[str, object]) -> dict[str, bytes]:
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CheckError("manifest has no source files")
    decoded: dict[str, bytes] = {}
    normalized_entries: list[dict[str, object]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise CheckError("manifest source entry is not an object")
        path = raw.get("path")
        if not isinstance(path, str):
            raise CheckError("manifest source entry has no path")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or str(pure) != path:
            raise CheckError(f"unsafe manifest path: {path!r}")
        if path in decoded:
            raise CheckError(f"duplicate manifest path: {path}")
        try:
            data = base64.b64decode(str(raw.get("data", "")), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CheckError(f"invalid base64 for {path}: {exc}") from exc
        if len(data) != raw.get("size"):
            raise CheckError(f"size mismatch for embedded {path}")
        if build_standalone.sha256(data) != raw.get("sha256"):
            raise CheckError(f"SHA-256 mismatch for embedded {path}")
        decoded[path] = data
        normalized_entries.append({"path": path, "sha256": raw["sha256"]})
    expected_tree = build_standalone.tree_digest(normalized_entries)
    if expected_tree != manifest.get("tree_sha256"):
        raise CheckError("manifest tree digest does not match its file entries")
    expected_id = f"{build_standalone.commit_token(str(manifest.get('source_commit', '')))}-{expected_tree[:16]}"
    if manifest.get("build_id") != expected_id:
        raise CheckError("manifest build ID does not match commit and tree digest")
    return decoded


def check_meta(artifact_text: str, manifest: dict[str, object]) -> None:
    for name, key in (
        ("bananaguard-source-commit", "source_commit"),
        ("bananaguard-build-id", "build_id"),
    ):
        pattern = re.compile(META_RE_TEMPLATE.format(name=re.escape(name)), re.IGNORECASE | re.DOTALL)
        match = pattern.search(artifact_text)
        if not match or match.group(1) != str(manifest.get(key, "")):
            raise CheckError(f"{name} metadata is missing or inconsistent")


def check_font_licenses(artifact_text: str, decoded: dict[str, bytes]) -> None:
    license_bytes = decoded.get(build_standalone.FONT_LICENSE_REL)
    if license_bytes is None:
        raise CheckError(
            f"source manifest is missing {build_standalone.FONT_LICENSE_REL}"
        )
    match = FONT_LICENSE_RE.search(artifact_text)
    if not match:
        raise CheckError("embedded third-party font license metadata is missing")
    try:
        metadata = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"embedded third-party font license metadata is invalid: {exc}") from exc
    try:
        license_text = license_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CheckError("font license notice is not valid UTF-8") from exc
    expected = {
        "path": build_standalone.FONT_LICENSE_REL,
        "sha256": build_standalone.sha256(license_bytes),
        "text": license_text,
    }
    if metadata != expected:
        raise CheckError("embedded third-party font license metadata differs from LICENSES.txt")
    link = FONT_LICENSE_LINK_RE.search(artifact_text)
    if not link:
        raise CheckError("human-readable third-party font license link is missing")
    encoded = link.group("href").split(",", 1)[1]
    try:
        linked_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CheckError(f"font license link contains invalid base64: {exc}") from exc
    if linked_bytes != license_bytes:
        raise CheckError("font license link differs from LICENSES.txt")
    for required in (
        "Copyright 2019 The Manrope Project Authors",
        "Copyright 2020 The Space Grotesk Project Authors",
        "Copyright 2026 Google LLC.  All Rights Reserved.",
        "SIL OPEN FONT LICENSE Version 1.1",
        "Apache License",
        "Version 2.0, January 2004",
    ):
        if required not in license_text:
            raise CheckError(f"font license notice is incomplete: missing {required!r}")
    if set(EXPECTED_FONT_SHA256).issubset(decoded):
        for path, expected_sha in EXPECTED_FONT_SHA256.items():
            actual_sha = build_standalone.sha256(decoded[path])
            if actual_sha != expected_sha:
                raise CheckError(
                    f"vendored font bytes differ from the licensed binary: {path}"
                )
        normalized_license = license_text.replace("\r\n", "\n").replace("\r", "\n")
        if build_standalone.sha256(normalized_license.encode("utf-8")) != EXPECTED_FONT_LICENSE_SHA256:
            raise CheckError("LICENSES.txt differs from the reviewed full notices and license texts")


def normalized_sha256(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CheckError("a required license is not valid UTF-8") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return build_standalone.sha256(normalized.encode("utf-8"))


def check_distribution_licenses(artifact_text: str, decoded: dict[str, bytes]) -> None:
    parser = LicenseReferenceParser()
    parser.feed(artifact_text)
    for path in build_standalone.LICENSE_RELS:
        expected = decoded.get(path)
        if expected is None:
            raise CheckError(f"source manifest is missing required license {path}")
        hrefs = parser.references.get(path, [])
        if not hrefs:
            raise CheckError(f"standalone has no readable link for required license {path}")
        matched = False
        for href in hrefs:
            prefix = "data:text/plain"
            if not href.startswith(prefix) or ";base64," not in href:
                continue
            try:
                candidate = base64.b64decode(href.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error):
                continue
            if candidate == expected:
                matched = True
                break
        if not matched:
            raise CheckError(f"readable license link differs from manifest source {path}")
        if path not in parser.anchor_paths:
            raise CheckError(f"standalone has no user-visible anchor for required license {path}")

    if set(EXPECTED_JAVASCRIPT_SHA256).issubset(decoded):
        for path, expected_sha in EXPECTED_JAVASCRIPT_SHA256.items():
            if build_standalone.sha256(decoded[path]) != expected_sha:
                raise CheckError(f"vendored JavaScript differs from reviewed binary: {path}")
        if normalized_sha256(decoded[build_standalone.VENDOR_LICENSE_REL]) != EXPECTED_VENDOR_LICENSE_SHA256:
            raise CheckError("vendor/LICENSES.txt differs from reviewed React and Modernizr notices")
        if normalized_sha256(decoded[build_standalone.PROJECT_LICENSE_REL]) != EXPECTED_PROJECT_LICENSE_SHA256:
            raise CheckError("root LICENSE differs from the reviewed BananaGuard MIT license")
        vendor_text = decoded[build_standalone.VENDOR_LICENSE_REL].decode("utf-8")
        for required in (
            "React 18.3.1 (react.production.min.js)",
            "ReactDOM 18.3.1-next-f1338f8080-20240426",
            "Copyright (c) Facebook, Inc. and its affiliates.",
            "Modernizr 3.0.0pre (Custom Build) | MIT",
            "Copyright (c) 2009-2018 The Modernizr Team",
            "MIT License",
            'THE SOFTWARE IS PROVIDED "AS IS"',
        ):
            if required not in vendor_text:
                raise CheckError(f"vendor JavaScript license notice is incomplete: missing {required!r}")


def check_source_parity(root: Path, decoded: dict[str, bytes]) -> None:
    expected_paths = {
        build_standalone.repo_relative(path, root): path
        for path in build_standalone.collect_source_paths(root)
    }
    if set(decoded) != set(expected_paths):
        missing = sorted(set(expected_paths) - set(decoded))
        extra = sorted(set(decoded) - set(expected_paths))
        raise CheckError(f"source input set differs (missing={missing}, extra={extra})")
    for rel, path in expected_paths.items():
        if decoded[rel] != path.read_bytes():
            raise CheckError(f"artifact is stale: embedded {rel} differs from current source")


def check_legacy_phrases(decoded: dict[str, bytes]) -> None:
    for path, data in decoded.items():
        if Path(path).suffix.lower() not in {".html", ".js", ".css", ".mjs"}:
            continue
        text = data.decode("utf-8", errors="replace")
        for label, pattern in LEGACY_PATTERNS:
            match = pattern.search(text)
            if match:
                excerpt = " ".join(text[max(0, match.start() - 30) : match.end() + 30].split())
                raise CheckError(f"legacy {label} found in {path}: {excerpt!r}")


def extract_sources(decoded: dict[str, bytes], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    root = output.resolve()
    for rel, data in decoded.items():
        destination = (root / Path(*PurePosixPath(rel).parts)).resolve()
        destination.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def check_artifact(artifact: Path, root: Path, extract: Path | None = None) -> dict[str, object]:
    try:
        artifact_text = artifact.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(f"cannot read standalone artifact {artifact}: {exc}") from exc
    manifest = read_manifest(artifact_text)
    decoded = decode_entries(manifest)
    check_meta(artifact_text, manifest)
    check_font_licenses(artifact_text, decoded)
    check_distribution_licenses(artifact_text, decoded)
    check_source_parity(root.resolve(), decoded)
    check_legacy_phrases(decoded)
    parser = RuntimeDependencyParser()
    parser.feed(artifact_text)
    if parser.errors:
        raise CheckError("; ".join(parser.errors))
    if "data-bg-bundle-bootstrap" not in artifact_text:
        raise CheckError("embedded component fetch bootstrap is missing")
    if extract is not None:
        extract_sources(decoded, extract)
    return {
        "build_id": manifest["build_id"],
        "source_commit": manifest["source_commit"],
        "file_count": len(decoded),
        "bytes": artifact.stat().st_size,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", nargs="?", type=Path, default=build_standalone.DEFAULT_OUTPUT)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--extract", type=Path, help="also reconstruct exact bundled inputs into this directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact = args.artifact if args.artifact.is_absolute() else args.root / args.artifact
    try:
        result = check_artifact(artifact, args.root, args.extract)
    except (CheckError, build_standalone.BuildError) as exc:
        print(f"standalone check failed: {exc}", file=sys.stderr)
        return 1
    print(
        "verified {file_count} inputs in {bytes} byte standalone (build {build_id}, source {source_commit})".format(
            **result
        )
    )
    if args.extract:
        print(f"extracted exact source inputs to {args.extract}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
