#!/usr/bin/env python3
"""Build a deterministic, fully offline BananaGuard standalone HTML file.

Only Python's standard library is required. The generated file contains:

* inline copies of local scripts and stylesheets;
* data-URI copies of fonts and other stylesheet assets;
* an in-memory fetch shim for sibling Design Component files and assets; and
* an exact source manifest that the parity checker can validate or extract.

No timestamp is embedded, so identical inputs, source commit/dirty state, and
Python version-independent JSON settings produce identical output bytes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path("dist") / "BananaGuard-Standalone.html"
RUNTIME_SUFFIXES = {
    ".css",
    ".gif",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".mjs",
    ".png",
    ".svg",
    ".txt",
    ".webp",
    ".woff",
    ".woff2",
}
MIME_TYPES = {
    ".css": "text/css",
    ".gif": "image/gif",
    ".html": "text/html",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript",
    ".json": "application/json",
    ".mjs": "text/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

SCRIPT_SRC_RE = re.compile(
    r"<script\b(?P<before>[^>]*?)\bsrc\s*=\s*(?P<q>['\"])(?P<src>.*?)(?P=q)"
    r"(?P<after>[^>]*)>\s*</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
LINK_RE = re.compile(r"<link\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
STYLE_RE = re.compile(r"<style\b(?P<attrs>[^>]*)>(?P<body>.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
CSS_URL_RE = re.compile(r"url\(\s*(?P<q>['\"]?)(?P<url>.*?)(?P=q)\s*\)", re.IGNORECASE)
STATIC_SRC_RE = re.compile(
    r"(?P<prefix><(?:img|source|video|audio|input)\b[^>]*?\bsrc\s*=\s*)"
    r"(?P<q>['\"])(?P<src>.*?)(?P=q)",
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE_TEMPLATE = r"\b{attr}\s*=\s*(['\"])(.*?)\1"
FONT_LICENSE_REL = "vendor/fonts/LICENSES.txt"
PROJECT_LICENSE_REL = "LICENSE"
VENDOR_LICENSE_REL = "vendor/LICENSES.txt"
LICENSE_RELS = (PROJECT_LICENSE_REL, VENDOR_LICENSE_REL, FONT_LICENSE_REL)


class BuildError(RuntimeError):
    """Raised when the canonical dashboard cannot be bundled safely."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repo_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def mime_type(path: Path) -> str:
    if path.name == "LICENSE":
        return "text/plain"
    return MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildError(f"cannot read UTF-8 source {path}: {exc}") from exc


def source_commit(root: Path, explicit: str | None = None) -> str:
    candidate = explicit or os.environ.get("BANANAGUARD_SOURCE_COMMIT")
    if candidate:
        return candidate.strip()
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    try:
        source_paths = [repo_relative(path, root) for path in collect_source_paths(root)]
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *source_paths],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError, BuildError, ValueError):
        return commit
    return commit + "-dirty" if status else commit


def commit_token(commit: str) -> str:
    """Return the compact provenance token used in deterministic build IDs."""
    dirty_suffix = "-dirty"
    if commit.endswith(dirty_suffix):
        return commit[: -len(dirty_suffix)][:12] + dirty_suffix
    return commit[:12]


def collect_source_paths(root: Path) -> list[Path]:
    """Return the canonical runtime inputs in deterministic path order."""
    required = [
        root / "index.html",
        root / "support.js",
        root / "Ring.dc.html",
        root / PROJECT_LICENSE_REL,
        root / VENDOR_LICENSE_REL,
    ]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise BuildError("missing required runtime input(s): " + ", ".join(map(str, missing)))

    paths: set[Path] = set(required)
    paths.update(path for path in root.glob("*.dc.html") if path.is_file())
    for dirname in ("vendor", "assets"):
        directory = root / dirname
        if not directory.is_dir():
            continue
        paths.update(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in RUNTIME_SUFFIXES
        )

    bundled_fonts = [
        path for path in paths
        if path.suffix.lower() in {".woff", ".woff2"}
    ]
    font_license = root / FONT_LICENSE_REL
    if bundled_fonts and not font_license.is_file():
        raise BuildError(
            f"vendored fonts require the redistributable notice file {FONT_LICENSE_REL}"
        )
    if bundled_fonts:
        paths.add(font_license)

    # Also capture future root-level assets referenced directly by index.html;
    # the source manifest must never omit a file merely because it lives
    # outside today's vendor/assets layout.
    index_text = read_utf8(root / "index.html")
    direct_refs = [match.group("src") for match in SCRIPT_SRC_RE.finditer(index_text)]
    direct_refs.extend(
        ref
        for match in LINK_RE.finditer(index_text)
        if (ref := attr_value(match.group("attrs"), "href"))
    )
    direct_refs.extend(match.group("src") for match in STATIC_SRC_RE.finditer(index_text))
    for ref in direct_refs:
        local = resolve_local(ref, root / "index.html", root)
        if local is not None and local.is_file():
            paths.add(local)
            if local.suffix.lower() == ".css":
                for match in CSS_URL_RE.finditer(read_utf8(local)):
                    css_ref = match.group("url").strip()
                    if not css_ref or css_ref.startswith(("data:", "#", "var(")):
                        continue
                    dependency = resolve_local(css_ref, local, root)
                    if dependency is not None and dependency.is_file():
                        paths.add(dependency)
    return sorted(paths, key=lambda path: repo_relative(path, root))


def source_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in collect_source_paths(root):
        data = path.read_bytes()
        entries.append(
            {
                "path": repo_relative(path, root),
                "size": len(data),
                "sha256": sha256(data),
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
    return entries


def tree_digest(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def attr_value(attrs: str, name: str) -> str | None:
    match = re.search(ATTR_RE_TEMPLATE.format(attr=re.escape(name)), attrs, re.IGNORECASE | re.DOTALL)
    return match.group(2) if match else None


def strip_attrs(attrs: str, *names: str) -> str:
    for name in names:
        attrs = re.sub(
            ATTR_RE_TEMPLATE.format(attr=re.escape(name)),
            "",
            attrs,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return " ".join(attrs.split())


def resolve_local(ref: str, base_file: Path, root: Path) -> Path | None:
    parsed = urlsplit(ref.strip())
    if parsed.scheme or parsed.netloc or parsed.path.startswith("//"):
        return None
    if not parsed.path or parsed.path.startswith("#"):
        return None
    candidate = (base_file.parent / unquote(parsed.path)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise BuildError(f"runtime path escapes repository root: {ref!r} in {base_file}") from exc
    return candidate


def data_uri(path: Path) -> str:
    return f"data:{mime_type(path)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def inline_css_urls(css: str, css_path: Path, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        ref = match.group("url").strip()
        if not ref or ref.startswith(("data:", "#", "var(")):
            return match.group(0)
        local = resolve_local(ref, css_path, root)
        if local is None:
            raise BuildError(f"external CSS dependency is not allowed in standalone: {ref}")
        if not local.is_file():
            raise BuildError(f"missing CSS dependency {ref!r} referenced by {repo_relative(css_path, root)}")
        return f'url("{data_uri(local)}")'

    return CSS_URL_RE.sub(replace, css)


def inline_scripts(document: str, index_path: Path, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        ref = match.group("src")
        local = resolve_local(ref, index_path, root)
        if local is None:
            raise BuildError(f"external script dependency is not allowed in standalone: {ref}")
        if not local.is_file():
            raise BuildError(f"missing script dependency: {ref}")
        rel = repo_relative(local, root)
        attrs = strip_attrs(match.group("before") + " " + match.group("after"), "src", "integrity", "crossorigin")
        prefix = (" " + attrs) if attrs else ""
        source = read_utf8(local).replace("</script", "<\\/script")
        return (
            f'<script{prefix} data-bg-bundle-path="{html_lib.escape(rel, quote=True)}" '
            f'data-bg-source-sha256="{sha256(local.read_bytes())}">\n{source}\n</script>'
        )

    return SCRIPT_SRC_RE.sub(replace, document)


def inline_links(document: str, index_path: Path, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        ref = attr_value(attrs, "href")
        rel_value = (attr_value(attrs, "rel") or "").lower().split()
        if not ref:
            return match.group(0)
        if "preconnect" in rel_value or "dns-prefetch" in rel_value:
            return ""
        local = resolve_local(ref, index_path, root)
        if "stylesheet" in rel_value:
            if local is None:
                raise BuildError(f"external stylesheet dependency is not allowed in standalone: {ref}")
            if not local.is_file():
                raise BuildError(f"missing stylesheet dependency: {ref}")
            rel = repo_relative(local, root)
            css = inline_css_urls(read_utf8(local), local, root).replace("</style", "<\\/style")
            return (
                f'<style data-bg-bundle-path="{html_lib.escape(rel, quote=True)}" '
                f'data-bg-source-sha256="{sha256(local.read_bytes())}">\n{css}\n</style>'
            )
        if local is not None:
            if not local.is_file():
                raise BuildError(f"missing linked runtime dependency: {ref}")
            escaped = html_lib.escape(data_uri(local), quote=True)
            return re.sub(
                ATTR_RE_TEMPLATE.format(attr="href"),
                f'href="{escaped}"',
                match.group(0),
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        return match.group(0)

    return LINK_RE.sub(replace, document)


def inline_license_anchors(document: str, index_path: Path, root: Path) -> str:
    """Make the user-facing project/third-party license links offline-safe."""
    expected = set(LICENSE_RELS)

    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        ref = attr_value(attrs, "href")
        if not ref:
            return match.group(0)
        local = resolve_local(ref, index_path, root)
        if local is None:
            return match.group(0)
        rel = repo_relative(local, root)
        if rel not in expected:
            return match.group(0)
        if not local.is_file():
            raise BuildError(f"missing linked license dependency: {ref}")
        tag = re.sub(
            ATTR_RE_TEMPLATE.format(attr="href"),
            f'href="{html_lib.escape(data_uri(local), quote=True)}"',
            match.group(0),
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return tag[:-1] + (
            f' data-bg-bundle-path="{html_lib.escape(rel, quote=True)}"'
            f' data-bg-source-sha256="{sha256(local.read_bytes())}">'
        )

    return ANCHOR_RE.sub(replace, document)


def inline_style_blocks(document: str, index_path: Path, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        body = inline_css_urls(match.group("body"), index_path, root).replace("</style", "<\\/style")
        return f'<style{match.group("attrs")}>{body}</style>'

    return STYLE_RE.sub(replace, document)


def inline_static_sources(document: str, index_path: Path, root: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        ref = match.group("src")
        if ref.startswith(("data:", "blob:", "#")):
            return match.group(0)
        local = resolve_local(ref, index_path, root)
        if local is None:
            raise BuildError(f"external media dependency is not allowed in standalone: {ref}")
        if not local.is_file():
            raise BuildError(f"missing media dependency: {ref}")
        return f'{match.group("prefix")}"{html_lib.escape(data_uri(local), quote=True)}"'

    return STATIC_SRC_RE.sub(replace, document)


def embedded_fetch_resources(
    root: Path, index_document: str | None = None
) -> dict[str, dict[str, str]]:
    resources: dict[str, dict[str, str]] = {}
    for path in collect_source_paths(root):
        rel = repo_relative(path, root)
        if path.name == "support.js" or rel.startswith("vendor/"):
            continue
        if path.suffix.lower() != ".html" and not rel.startswith("assets/"):
            continue
        payload = (
            index_document.encode("utf-8")
            if rel == "index.html" and index_document is not None
            else path.read_bytes()
        )
        resources[rel] = {
            "mime": mime_type(path),
            "data": base64.b64encode(payload).decode("ascii"),
        }
    return resources


def bundle_prelude(
    manifest: dict[str, object],
    resources: dict[str, dict[str, str]],
    font_license: dict[str, str],
) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    build_json = json.dumps(
        {
            "schema_version": manifest["schema_version"],
            "source_commit": manifest["source_commit"],
            "build_id": manifest["build_id"],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    resources_json = json.dumps(resources, separators=(",", ":"), sort_keys=True)
    license_json = json.dumps(
        font_license, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).replace("</", "<\\/")
    license_data = base64.b64encode(font_license["text"].encode("utf-8")).decode("ascii")
    return f"""
<!-- BananaGuard deterministic standalone bundle (no build timestamp) -->
<meta name="bananaguard-source-commit" content="{html_lib.escape(str(manifest['source_commit']), quote=True)}">
<meta name="bananaguard-build-id" content="{html_lib.escape(str(manifest['build_id']), quote=True)}">
<link rel="license" title="BananaGuard third-party font licenses" data-bg-bundle-path="{FONT_LICENSE_REL}" data-bg-source-sha256="{font_license['sha256']}" href="data:text/plain;charset=utf-8;base64,{license_data}">
<script id="bananaguard-build-manifest" type="application/json">{manifest_json}</script>
<script id="bananaguard-third-party-font-licenses" type="application/json">{license_json}</script>
<script data-bg-bundle-bootstrap>
window.__BANANAGUARD_BUILD__=Object.freeze({build_json});
(function(){{
  "use strict";
  var resources={resources_json};
  window.__BANANAGUARD_EMBEDDED_RESOURCES__=Object.freeze(resources);
  var nativeFetch=window.fetch.bind(window);
  window.fetch=function(input,init){{
    var raw=typeof input==="string"?input:(input&&input.url)||String(input);
    var key=null;
    try{{
      var parsed=new URL(raw,window.location.href);
      var current=new URL(window.location.href);
      var path=decodeURIComponent(parsed.pathname).replace(/^[/]+/,"");
      var parts=path.split("/");
      var base=parts[parts.length-1];
      if(parsed.protocol===current.protocol&&parsed.host===current.host&&parsed.pathname===current.pathname&&Object.prototype.hasOwnProperty.call(resources,"index.html")) key="index.html";
      else if(Object.prototype.hasOwnProperty.call(resources,path)) key=path;
      else if(Object.prototype.hasOwnProperty.call(resources,base)) key=base;
    }}catch(_error){{}}
    if(key){{
      var entry=resources[key];
      var binary=atob(entry.data);
      var bytes=new Uint8Array(binary.length);
      for(var i=0;i<binary.length;i+=1) bytes[i]=binary.charCodeAt(i);
      return Promise.resolve(new Response(bytes,{{status:200,headers:{{"Content-Type":entry.mime,"Cache-Control":"no-store"}}}}));
    }}
    return nativeFetch(input,init);
  }};
}})();
</script>
"""


def assert_no_direct_runtime_dependencies(document: str) -> None:
    if re.search(r"<script\b[^>]*\bsrc\s*=", document, re.IGNORECASE):
        raise BuildError("standalone still contains a script src dependency")
    if re.search(r"<link\b[^>]*\brel\s*=\s*['\"][^'\"]*stylesheet", document, re.IGNORECASE):
        raise BuildError("standalone still contains a linked stylesheet dependency")
    if re.search(r"<(?:img|source|video|audio)\b[^>]*\bsrc\s*=\s*['\"](?:https?:)?//", document, re.IGNORECASE):
        raise BuildError("standalone still contains an external media dependency")
    if re.search(r"\bsrcset\s*=", document, re.IGNORECASE):
        raise BuildError("srcset is not yet supported by the deterministic bundler")


def build(root: Path, output: Path, explicit_commit: str | None = None) -> dict[str, object]:
    root = root.resolve()
    index_path = root / "index.html"
    entries = source_entries(root)
    digest = tree_digest(entries)
    commit = source_commit(root, explicit_commit)
    build_id = f"{commit_token(commit)}-{digest[:16]}"
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": commit,
        "tree_sha256": digest,
        "build_id": build_id,
        "files": entries,
    }

    document = read_utf8(index_path)
    document = inline_links(document, index_path, root)
    document = inline_license_anchors(document, index_path, root)
    document = inline_style_blocks(document, index_path, root)
    document = inline_static_sources(document, index_path, root)
    # support.js refetches the current page before it hydrates the dynamic
    # template. Preserve the rewritten, dependency-free document for that
    # same-document fetch; returning the raw source would restore file-relative
    # license and media links after hydration.
    fetch_document = document
    # Inline external scripts last so HTML-like strings inside JavaScript are
    # never mistaken for document-level style, link, or media elements.
    document = inline_scripts(document, index_path, root)
    head = re.search(r"<head\b[^>]*>", document, re.IGNORECASE)
    if not head:
        raise BuildError("index.html has no <head> element")
    license_path = root / FONT_LICENSE_REL
    license_bytes = license_path.read_bytes()
    font_license = {
        "path": FONT_LICENSE_REL,
        "sha256": sha256(license_bytes),
        # Preserve exact newline bytes in the redistributable notice. Text-mode
        # reads normalize CRLF on Windows and would make the readable metadata
        # disagree with the manifest entry even though both came from one file.
        "text": license_bytes.decode("utf-8"),
    }
    prelude = bundle_prelude(
        manifest, embedded_fetch_resources(root, fetch_document), font_license
    )
    document = document[: head.end()] + prelude + document[head.end() :]
    assert_no_direct_runtime_dependencies(document)

    output = output if output.is_absolute() else root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8", newline="\n")
    return {
        "output": str(output),
        "build_id": build_id,
        "source_commit": commit,
        "tree_sha256": digest,
        "file_count": len(entries),
        "bytes": output.stat().st_size,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit", help="override the embedded source commit (CI normally uses git HEAD)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build(args.root, args.output, args.source_commit)
    except BuildError as exc:
        print(f"standalone build failed: {exc}", file=sys.stderr)
        return 1
    print(
        "built {output} ({bytes} bytes, {file_count} inputs, build {build_id})".format(**result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
