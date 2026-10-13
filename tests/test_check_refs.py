"""Unit tests for scripts/check_refs.py (git and docker mocked)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.check_refs import check_catalog

DIGEST_A = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
DIGEST_B = "0000000000000000000000000000000000000000000000000000000000000000"
DIGEST_MINIO = "14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"


def _write_catalog(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.dump(document, sort_keys=False), encoding="utf-8")
    return path


def _base_catalog() -> dict:
    return {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {
                        "version": "1.0.0",
                        "ref": "v1.0.0",
                        "keystone_mcp": ">=0.5.0,<2.0",
                    }
                ],
            },
            "minio": {
                "kind": "resource",
                "provides": {"kind": "s3", "version": 1},
                "source": "https://example.com/minio",
                "versions": [{"version": "1.0.0", "ref": "v1.0.0"}],
            },
            "postgres": {
                "kind": "resource",
                "provides": {"kind": "postgres", "version": 1},
                "source": "https://example.com/postgres",
                "versions": [{"version": "1.0.0", "ref": "v1.0.0"}],
            },
            "hello-upstream": {
                "source": "https://example.com/hello",
                "versions": [
                    {
                        "version": "1.0.0",
                        "ref": "v1.0.0",
                        "keystone_mcp": ">=0.5.0,<2.0",
                    }
                ],
            },
        },
        "bundles": {
            "minimal": {
                "description": "File transfer for agents.",
                "packages": ["file-mcp"],
            }
        },
    }


def _service_manifest(
    name: str,
    *,
    version: str = "1.0.0",
    image: str | None = None,
) -> str:
    img = image or f"ghcr.io/keystone-codes/{name}@sha256:{DIGEST_A}"
    return textwrap.dedent(
        f"""\
        schema_version: 1
        kind: service
        name: {name}
        version: {version}
        compat:
          keystone_mcp: ">=0.5.0,<2.0"
        mcp:
          container: mcp
          transport: http
          path: /mcp
          healthz: /healthz
          namespace: ns
          bearer_env: TOKEN
          tools: [t]
        identity:
          signed_assertion: required
          secret_env: SEC
          audience_env: AUD
        containers:
          mcp:
            image: {img}
            port: 8081
            env: []
            secrets: []
            volumes: []
        needs: []
        requires: []
        """
    )


def _resource_manifest_s3() -> str:
    return textwrap.dedent(
        f"""\
        schema_version: 1
        kind: resource
        name: minio
        version: 1.0.0
        provides: {{kind: s3, version: 1, container: server}}
        containers:
          server:
            image: minio/minio@sha256:{DIGEST_MINIO}
            port: 9000
            env: []
            volumes: []
            secrets: []
        provision:
          image: minio/mc@sha256:{DIGEST_B}
          script: provision.sh
          admin_env: {{user: U, password: P}}
        """
    )


def _resource_manifest_postgres() -> str:
    digest = "721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
    return textwrap.dedent(
        f"""\
        schema_version: 1
        kind: resource
        name: postgres
        version: 1.0.0
        provides: {{kind: postgres, version: 1, container: db}}
        containers:
          db:
            image: postgres:16-alpine@sha256:{digest}
            port: 5432
            env: []
            volumes: []
            secrets: []
        provision:
          image: postgres:16-alpine@sha256:{digest}
          script: provision.sh
          admin_env: {{user: U, password: P}}
        """
    )


class FakeRunner:
    def __init__(self, manifests: dict[str, str], *, tag_missing: set[str] | None = None) -> None:
        self.manifests = manifests
        self.tag_missing = tag_missing or set()
        self.inspect_calls: list[str] = []
        self.tag_digests: dict[str, str] = {}

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if cmd[:3] == ["git", "ls-remote", "--tags"]:
            ref = cmd[-1]
            source = cmd[-2]
            key = f"{source}:{ref}"
            if key in self.tag_missing or ref.endswith("/missing"):
                return 0, ""
            return 0, f"deadbeef\t{ref}\n"

        if cmd[:2] == ["git", "clone"]:
            dest = Path(cmd[-1])
            source = cmd[-2]
            dest.mkdir(parents=True, exist_ok=True)
            body = self.manifests.get(source, _service_manifest("unknown"))
            (dest / "keystone-service.yaml").write_text(body, encoding="utf-8")
            return 0, ""

        if cmd[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            target = cmd[4]
            self.inspect_calls.append(target)
            if "--format" in cmd:
                tag_ref = cmd[4]
                digest = self.tag_digests.get(tag_ref, f"sha256:{DIGEST_A}")
                return 0, json_quote_digest(digest)
            if "missing" in target:
                return 1, "not found"
            return 0, "ok"

        if cmd and cmd[0] == "lint-ok":
            return 0, ""
        if cmd and cmd[0] == "lint-fail":
            return 3, "manifest_invalid: bad field\n"

        return 0, ""


def json_quote_digest(digest: str) -> str:
    return f'"{digest}"'


def test_valid_catalog_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catalog = _base_catalog()
    manifests = {
        "https://example.com/file-mcp": _service_manifest("file-mcp"),
        "https://example.com/minio": _resource_manifest_s3(),
        "https://example.com/postgres": _resource_manifest_postgres(),
        "https://example.com/hello": _service_manifest("hello-upstream", image=f"ghcr.io/keystone-codes/hello-upstream@sha256:{DIGEST_B}"),
    }
    runner = FakeRunner(manifests)
    runner.tag_digests = {
        "ghcr.io/keystone-codes/file-mcp:1.0.0": f"sha256:{DIGEST_A}",
        "ghcr.io/keystone-codes/hello-upstream:1.0.0": f"sha256:{DIGEST_B}",
    }
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 0
    assert errors == []
    assert capsys.readouterr().out.strip() == "ok: 4 packages, 4 versions checked"


def test_all_container_images_checked(tmp_path: Path) -> None:
    manifest = textwrap.dedent(
        f"""\
        schema_version: 1
        kind: service
        name: file-mcp
        version: 1.0.0
        compat:
          keystone_mcp: ">=0.5.0,<2.0"
        mcp:
          container: mcp
          transport: http
          path: /mcp
          healthz: /healthz
          namespace: ns
          bearer_env: TOKEN
          tools: [t]
        identity:
          signed_assertion: required
          secret_env: SEC
          audience_env: AUD
        containers:
          api:
            image: ghcr.io/keystone-codes/file-mcp@sha256:{DIGEST_A}
            port: 8080
            env: []
            secrets: []
            volumes: []
          migrate:
            image: ghcr.io/keystone-codes/file-mcp@sha256:{DIGEST_B}
            port: 8082
            env: []
            secrets: []
            volumes: []
          mcp:
            image: ghcr.io/keystone-codes/file-mcp:1.0.0
            port: 8081
            env: []
            secrets: []
            volumes: []
        needs: []
        requires: []
        """
    )
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner({"https://example.com/file-mcp": manifest})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert len([e for e in errors if "containers.mcp.image" in e]) == 1
    digest_inspects = [c for c in runner.inspect_calls if "@sha256:" in c]
    assert len(digest_inspects) == 2


def test_missing_tag_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner(
        {"https://example.com/file-mcp": _service_manifest("file-mcp")},
        tag_missing={"https://example.com/file-mcp:refs/tags/v1.0.0"},
    )
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert any("tag v1.0.0 not found" in e for e in errors)


def test_manifest_name_or_version_mismatch_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner(
        {"https://example.com/file-mcp": _service_manifest("file-mcp", version="1.0.1")}
    )
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    joined = "\n".join(errors)
    assert "1.0.0" in joined and "1.0.1" in joined


def test_resource_provides_mismatch_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "minio": {
                "kind": "resource",
                "provides": {"kind": "s3", "version": 1},
                "source": "https://example.com/minio",
                "versions": [{"version": "1.0.0", "ref": "v1.0.0"}],
            }
        },
    }
    bad = textwrap.dedent(
        f"""\
        schema_version: 1
        kind: resource
        name: minio
        version: 1.0.0
        provides: {{kind: postgres, version: 1, container: server}}
        containers:
          server:
            image: minio/minio@sha256:{DIGEST_MINIO}
            port: 9000
            env: []
            volumes: []
            secrets: []
        provision:
          image: minio/mc@sha256:{DIGEST_B}
          script: provision.sh
          admin_env: {{user: U, password: P}}
        """
    )
    runner = FakeRunner({"https://example.com/minio": bad})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert any("provides.kind" in e for e in errors)


def test_compat_mismatch_reported(tmp_path: Path) -> None:
    manifest = _service_manifest("file-mcp").replace(
        ">=0.5.0,<2.0", ">=0.6.0,<2.0", 1
    )
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner({"https://example.com/file-mcp": manifest})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert any("compat.keystone_mcp" in e for e in errors)


def test_unpinned_image_reported(tmp_path: Path) -> None:
    manifest = _service_manifest(
        "file-mcp",
        image="ghcr.io/keystone-codes/file-mcp:1.0.0",
    )
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner({"https://example.com/file-mcp": manifest})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert any("containers.mcp.image" in e for e in errors)


def test_tag_digest_differs_from_manifest_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner({"https://example.com/file-mcp": _service_manifest("file-mcp")})
    runner.tag_digests = {"ghcr.io/keystone-codes/file-mcp:1.0.0": f"sha256:{DIGEST_B}"}
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 1
    assert any("does not match manifest digest" in e for e in errors)


def test_foreign_image_checked_for_existence_only(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "minio": {
                "kind": "resource",
                "provides": {"kind": "s3", "version": 1},
                "source": "https://example.com/minio",
                "versions": [{"version": "1.0.0", "ref": "v1.0.0"}],
            }
        },
    }
    runner = FakeRunner({"https://example.com/minio": _resource_manifest_s3()})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=runner)
    assert code == 0
    tag_checks = [c for c in runner.inspect_calls if ":1.0.0" in c]
    assert tag_checks == []


def test_bundle_unknown_package_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {},
        "bundles": {"minimal": {"description": "x", "packages": ["missing-pkg"]}},
    }
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=lambda _cmd: (0, ""))
    assert code == 1
    assert any("unknown package" in e for e in errors)


def test_bundle_with_resource_reported(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "minio": {
                "kind": "resource",
                "provides": {"kind": "s3", "version": 1},
                "source": "https://example.com/minio",
                "versions": [{"version": "1.0.0", "ref": "v1.0.0"}],
            }
        },
        "bundles": {"all": {"description": "x", "packages": ["minio"]}},
    }
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-ok"], run=lambda _cmd: (0, ""))
    assert code == 1
    assert any("resource" in e and "needs" in e for e in errors)


def test_lint_failure_reported_with_output(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
    }
    runner = FakeRunner({"https://example.com/file-mcp": _service_manifest("file-mcp")})
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-fail"], run=runner)
    assert code == 1
    assert len(errors) == 1
    assert "manifest_invalid" in errors[0]


def test_all_errors_listed_not_first_only(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "name": "keystone-codes",
        "packages": {
            "file-mcp": {
                "source": "https://example.com/file-mcp",
                "versions": [
                    {"version": "1.0.0", "ref": "v1.0.0", "keystone_mcp": ">=0.5.0,<2.0"}
                ],
            }
        },
        "bundles": {"minimal": {"description": "x", "packages": ["ghost"]}},
    }
    runner = FakeRunner(
        {"https://example.com/file-mcp": _service_manifest("wrong-name")},
        tag_missing={"https://example.com/file-mcp:refs/tags/v1.0.0"},
    )
    path = _write_catalog(tmp_path, catalog)
    code, errors = check_catalog(path, ["lint-fail"], run=runner)
    assert code == 1
    assert len(errors) >= 3
