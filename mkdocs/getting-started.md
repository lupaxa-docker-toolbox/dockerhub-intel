# Getting Started

## Requirements

- Python 3.11 or later
- macOS, Linux, or Windows

## Install

From a checkout of this repository:

```bash
make init
make python-install-dev
```

Confirm the command is available:

```bash
dockerhub-intel --version
dockerhub-intel --help
```

## Configuration

The default directory follows platform conventions. On macOS and Linux that is:

```text
~/.config/dockerhub-intel/
├── config.yml
├── profiles.yml
└── repositories.yml
```

`config.yml` points at the profile and repository files. A path in `--config` replaces the main file. Relative paths in that file are resolved from the main file's directory.

Credentials are not stored in these files. Public repositories need no token.

Pass `--token` or set `DOCKERHUB_TOKEN` when a request must be authenticated. The command-line value wins.
A blank value is treated as no token. Personal access tokens use the `JWT` scheme; tokens that are already JWTs use `Bearer`.

## First Repository

A repository entry is either a name or an object. These are equivalent for the default profile and group:

```yaml
repositories:
  - nginx
  - repo: library/redis
    group: official-images
    profile: watchlist
```

`nginx` is stored as `library/nginx`. A repository without a group belongs to `ungrouped`.
