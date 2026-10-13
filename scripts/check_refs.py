#!/usr/bin/env python3
"""Verify catalog package refs, manifests, images, and bundles."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

IMAGE_DIGEST_RE = re.compile(
    r"^(?P<repo>[^@]+)@sha256:(?P<digest>[a-f0-9]{64})$",
    re.IGNORECASE,
)
KEYSTONE_CODES_GHCR = re.compile(r"^ghcr\.io/keystone-codes/")

RunFn = Callable[[list[str]], tuple[int, str]]


def default_run(cmd: list[str]) -> tuple[int, str]:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, out


def _version_str(value: object) -> str:
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return value
    return str(value)


def _load_manifest(repo_dir: Path, pkg_path: str) -> dict[str, Any]:
    manifest_path = repo_dir / pkg_path / "keystone-service.yaml"
    if not manifest_path.is_file():
        manifest_path = repo_dir / "keystone-service.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{manifest_path}: keystone-service.yaml not found")
    document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{manifest_path}: manifest must be a mapping")
    return document


def _collect_images(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    images: list[tuple[str, str]] = []
    containers = manifest.get("containers")
    if isinstance(containers, dict):
        for role, spec in containers.items():
            if not isinstance(spec, dict):
                continue
            image = spec.get("image")
            if isinstance(image, str):
                images.append((role, image))
    if manifest.get("kind") == "resource":
        provision = manifest.get("provision")
        if isinstance(provision, dict):
            image = provision.get("image")
            if isinstance(image, str):
                images.append(("provision", image))
    return images


def _image_field_label(role: str) -> str:
    if role == "provision":
        return "provision.image"
    return f"containers.{role}.image"


def _check_image_format(role: str, image: str, pkg: str, version: str) -> str | None:
    if not IMAGE_DIGEST_RE.match(image):
        label = _image_field_label(role)
        return f"{pkg} {version}: {label} must be pinned by digest, got {image!r}"
    return None


def _repo_from_digest_image(image: str) -> str:
    return image.split("@", 1)[0]


def _digest_from_image(image: str) -> str:
    match = IMAGE_DIGEST_RE.match(image)
    if not match:
        return ""
    return f"sha256:{match.group('digest').lower()}"


def _check_image_exists(
    run: RunFn,
    role: str,
    image: str,
    pkg: str,
    version: str,
    verified_tags: set[str],
) -> list[str]:
    errors: list[str] = []
    fmt_err = _check_image_format(role, image, pkg, version)
    if fmt_err:
        return [fmt_err]
    code, out = run(["docker", "buildx", "imagetools", "inspect", image])
    if code != 0:
        errors.append(
            f"{pkg} {version}: image {image!r} not found in registry ({out.strip()})"
        )
        return errors
    if KEYSTONE_CODES_GHCR.match(_repo_from_digest_image(image)):
        repo = _repo_from_digest_image(image)
        expected = _digest_from_image(image)
        errors.extend(
            _verify_keystone_codes_tag(
                run, repo, version, expected, pkg, version, verified_tags
            )
        )
    return errors


def _compare_manifest(
    manifest: dict[str, Any],
    pkg_name: str,
    pkg_entry: dict[str, Any],
    version_row: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    version = _version_str(version_row["version"])
    prefix = f"{pkg_name} {version}"

    m_name = manifest.get("name")
    if m_name != pkg_name:
        errors.append(f"{prefix}: manifest name {m_name!r} != catalog {pkg_name!r}")

    m_version = _version_str(manifest.get("version"))
    if m_version != version:
        errors.append(
            f"{prefix}: manifest version {m_version!r} != catalog version {version!r}"
        )

    catalog_kind = pkg_entry.get("kind", "service")
    manifest_kind = manifest.get("kind", "service")
    if manifest_kind != catalog_kind:
        errors.append(
            f"{prefix}: manifest kind {manifest_kind!r} != catalog kind {catalog_kind!r}"
        )

    if catalog_kind == "resource":
        catalog_provides = pkg_entry.get("provides") or {}
        manifest_provides = manifest.get("provides") or {}
        if not isinstance(catalog_provides, dict) or not isinstance(manifest_provides, dict):
            errors.append(f"{prefix}: invalid provides in catalog or manifest")
        else:
            for key in ("kind", "version"):
                if manifest_provides.get(key) != catalog_provides.get(key):
                    errors.append(
                        f"{prefix}: manifest provides.{key} "
                        f"{manifest_provides.get(key)!r} != catalog {catalog_provides.get(key)!r}"
                    )

    catalog_compat = version_row.get("keystone_mcp")
    if catalog_kind == "service" and isinstance(catalog_compat, str):
        manifest_compat = (manifest.get("compat") or {}).get("keystone_mcp")
        if manifest_compat != catalog_compat:
            errors.append(
                f"{prefix}: manifest compat.keystone_mcp {manifest_compat!r} "
                f"!= catalog keystone_mcp {catalog_compat!r}"
            )

    return errors


def _check_bundles(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    packages = catalog.get("packages")
    if not isinstance(packages, dict):
        return errors
    bundles = catalog.get("bundles") or {}
    if not isinstance(bundles, dict):
        return errors
    for bundle_name, bundle in bundles.items():
        if not isinstance(bundle, dict):
            continue
        names = bundle.get("packages")
        if not isinstance(names, list):
            continue
        for index, pkg_name in enumerate(names):
            if pkg_name not in packages:
                errors.append(
                    f"bundles.{bundle_name}.packages[{index}]: unknown package {pkg_name!r}"
                )
                continue
            entry = packages[pkg_name]
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "service")
            if kind == "resource":
                errors.append(
                    f"bundles.{bundle_name}.packages[{index}]: {pkg_name!r} is a resource; "
                    "resources are installed through needs"
                )
    return errors


def _verify_keystone_codes_tag(
    run: RunFn,
    repo: str,
    version: str,
    expected_digest: str,
    pkg: str,
    version_label: str,
    verified_tags: set[str],
) -> list[str]:
    tag_ref = f"{repo}:{version}"
    if tag_ref in verified_tags:
        return []
    verified_tags.add(tag_ref)
    tag_code, tag_out = run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            tag_ref,
            "--format",
            "{{json .Manifest.Digest}}",
        ]
    )
    if tag_code != 0:
        return [
            f"{pkg} {version_label}: published tag {tag_ref!r} not found ({tag_out.strip()})"
        ]
    published = tag_out.strip().strip('"')
    if published.lower() != expected_digest.lower():
        return [
            f"{pkg} {version_label}: tag {version} digest {published} "
            f"does not match manifest digest {expected_digest}"
        ]
    return []


def check_catalog(
    catalog_path: Path,
    lint_cmd: list[str],
    run: RunFn = default_run,
) -> tuple[int, list[str]]:
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        return 1, ["catalog: root must be a mapping"]

    errors: list[str] = []
    errors.extend(_check_bundles(catalog))

    packages = catalog.get("packages")
    if not isinstance(packages, dict):
        return 1, errors or ["catalog: packages must be a mapping"]

    package_count = len(packages)
    version_count = 0
    verified_tags: set[str] = set()

    for pkg_name, pkg_entry in packages.items():
        if not isinstance(pkg_entry, dict):
            continue
        source = pkg_entry.get("source")
        if not isinstance(source, str):
            errors.append(f"{pkg_name}: missing source URL")
            continue
        pkg_path = pkg_entry.get("path", ".")
        if not isinstance(pkg_path, str):
            pkg_path = "."
        versions = pkg_entry.get("versions")
        if not isinstance(versions, list):
            continue

        for version_row in versions:
            if not isinstance(version_row, dict):
                continue
            version_count += 1
            version = _version_str(version_row.get("version", "?"))
            ref = version_row.get("ref")
            if not isinstance(ref, str):
                errors.append(f"{pkg_name} {version}: missing ref")
                continue

            tag_ref = ref if ref.startswith("refs/tags/") else f"refs/tags/{ref}"
            code, _out = run(["git", "ls-remote", "--tags", source, tag_ref])
            if code != 0 or not _out.strip():
                errors.append(f"{pkg_name} {version}: tag {ref} not found in {source}")

            with tempfile.TemporaryDirectory(prefix="ks-catalog-") as tmp:
                clone_dir = Path(tmp) / "repo"
                clone_code, clone_out = run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        ref,
                        source,
                        str(clone_dir),
                    ]
                )
                if clone_code != 0:
                    errors.append(
                        f"{pkg_name} {version}: clone failed ({clone_out.strip()})"
                    )
                    continue

                try:
                    manifest = _load_manifest(clone_dir, pkg_path)
                except (OSError, yaml.YAMLError, ValueError) as exc:
                    errors.append(f"{pkg_name} {version}: {exc}")
                    continue

                errors.extend(_compare_manifest(manifest, pkg_name, pkg_entry, version_row))

                manifest_file = clone_dir / pkg_path / "keystone-service.yaml"
                if not manifest_file.is_file():
                    manifest_file = clone_dir / "keystone-service.yaml"
                lint_code, lint_out = run([*lint_cmd, str(manifest_file)])
                if lint_code != 0:
                    detail = lint_out.strip() or f"lint exited with code {lint_code}"
                    errors.append(f"{pkg_name} {version}: lint failed: {detail}")

                for role, image in _collect_images(manifest):
                    errors.extend(
                        _check_image_exists(
                            run, role, image, pkg_name, version, verified_tags
                        )
                    )

    if errors:
        return 1, errors

    print(f"ok: {package_count} packages, {version_count} versions checked")
    return 0, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True, help="Path to catalog.yaml")
    parser.add_argument(
        "--lint-cmd",
        required=True,
        help='Shell command prefix for keystonectl lint, e.g. "uvx keystonectl lint"',
    )
    args = parser.parse_args(argv)

    if not shutil.which("git"):
        print("git not found in PATH", file=sys.stderr)
        return 1
    if not shutil.which("docker"):
        print("docker not found in PATH", file=sys.stderr)
        return 1

    lint_cmd = shlex.split(args.lint_cmd)
    if not lint_cmd:
        print("--lint-cmd must not be empty", file=sys.stderr)
        return 1

    code, errors = check_catalog(args.catalog, lint_cmd)
    for line in errors:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
