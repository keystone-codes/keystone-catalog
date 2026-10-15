# Changelog

## [Unreleased]

- Docs: point README and CI at the published `keystonectl` 0.1.0 (PyPI/`v0.1.0` tag) instead of a pinned commit SHA.
- `check_refs.py` reads a package's manifest only from its `path`; it no longer falls back to the repository root.
- If an image reference carries a tag before the digest, `check_refs.py` verifies that tag instead of the package version; this lets several packages share one image.

## 1.0.0

- Catalog `keystone-codes` with four packages: `file-mcp`, `hello-upstream`, and resource providers `minio` (s3/v1) and `postgres` (postgres/v1).
- Bundle `minimal` installs `file-mcp` only; resource providers are offered by the installer when a service declares `needs`.
