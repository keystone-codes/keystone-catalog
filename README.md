# keystone-catalog

Default **Keystone services catalog** for the `keystone-codes` publisher. `keystonectl` uses this repository out of the box; operators can add partner catalogs with `keystonectl catalog add`.

## What this is

The catalog lists installable packages (services and resource providers) and optional **bundles** — curated sets of services installed with one command (`keystonectl bundle install <name>`). Each package entry points at a git repository and a pinned tag; image digests live in the package manifest on that tag, not in this repo.

Trust model: HTTPS git URLs, immutable package tags, and container images pinned by digest. Catalog signing is planned later (see Keystone catalog concept, section 10).

## Packages and bundles

| Package | Kind | Role |
|---|---|---|
| `file-mcp` | service | Short-lived upload/download links for agents; needs an S3-compatible bucket (`needs: s3/v1`). |
| `hello-upstream` | service | Minimal demo MCP service for package authors; no storage or resources. |
| `minio` | resource | Provides `s3/v1` when no external S3 is configured. |
| `postgres` | resource | Provides `postgres/v1` when no external database is configured. |

**Resource providers** (`minio`, `postgres`) are not part of bundles. When a service declares `needs` and the operator has not connected an external resource, `keystonectl` offers a matching provider from the catalog (one provider per resource kind).

**Bundle `minimal`** currently contains only `file-mcp`. It is the default starter set for file transfer behind Keystone; more services will join as they are published. MinIO may be started automatically when `file-mcp` is installed without external S3.

## Adding a package

Changes land through a pull request:

1. The package meets the [Packaging a service](https://github.com/keystone-codes/keystonectl/blob/eda184ea8179534f31690322c53366d9947abc29/docs/packaging-a-service.md#checklist) checklist in `keystonectl`, including `X-Keystone-Identity` and bearer checks (R8).
2. The package repository has an immutable tag `v<version>`; `keystone-service.yaml` on that tag passes `keystonectl lint`.
3. Container images use `@sha256:…` digests; for `ghcr.io/keystone-codes/*`, the `:X.Y.Z` tag must reference the same digest as the manifest.
4. The PR adds a `packages.<name>` entry and a `CHANGELOG.md` line; CI (`check.yml`) must pass.
5. A catalog maintainer reviews and merges.

## Versions

- Package versions are semver and match the manifest on the pinned git tag.
- New releases add a row under `packages.<name>.versions`; older rows stay for `service upgrade` and rollback. Remove a version only for a documented security reason, with a CHANGELOG entry.
- Package tags are never moved or deleted; fixes ship as a new version.
- Renaming a tool or breaking input schemas requires a new major package version.
- This catalog repository is tagged `v<major>.<minor>.<patch>`: patch — metadata fixes; minor — new package or version; major — `schema_version` change. `keystonectl` pins a catalog source by tag.

## Trust

CI on every pull request runs `keystonectl lint` on `catalog.yaml`, unit tests for reference checking, and `scripts/check_refs.py` (git tags, manifest match, image digests). GitHub Actions use pinned action SHAs. Branch protection on `main` should require the `check` workflow (configured by the repository owner).

## Development

```bash
uv sync
uv run pytest -q
keystonectl lint catalog.yaml   # from a keystonectl install
uv run python scripts/check_refs.py --catalog catalog.yaml --lint-cmd "keystonectl lint"
```
