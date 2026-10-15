# Changelog

## 1.2.0

- New packages `atlassian` (Jira and Confluence) and `notion` (Notion): credential boxes for stdio MCP servers, both from the `stdio-box` repository.
- Catalog tag `v1.2.0`.

## 1.1.0

- Docs: point README and CI at the published `keystonectl` 0.1.0 (PyPI/`v0.1.0` tag) instead of a pinned commit SHA.
- `check_refs.py` reads a package's manifest only from its `path`; it no longer falls back to the repository root.
- If an image reference carries a tag before the digest, `check_refs.py` verifies that tag instead of the package version; this lets several packages share one image.
- `minio` 1.0.1: images rebuilt with the AGPL-3.0 text at `/licenses` and the license label; new digests.
- `hello-upstream` 1.0.1: the manifest summary says three tools.
- `file-mcp` 1.0.1: the manifest no longer calls the published digest a placeholder.
- Catalog tag `v1.1.0`.

## 1.0.0

- Catalog `keystone-codes` with four packages: `file-mcp`, `hello-upstream`, and resource providers `minio` (s3/v1) and `postgres` (postgres/v1).
- Bundle `minimal` installs `file-mcp` only; resource providers are offered by the installer when a service declares `needs`.
