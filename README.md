<p align="center">
  <a href="https://github.com/lupaxa-docker-toolbox">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/organisations/docker-toolbox/readme-logo.png" alt="The Lupaxa Project: Docker Toolbox" />
  </a>
</p>

<h1 align="center">Docker Hub Intel</h1>

**Docker Hub Intel** monitors an explicit list of Docker Hub repositories. It stores pull counts, tags, and image changes locally and can show them without a network connection. Groups organise the list. Profiles decide which checks run.

## Features

- An explicit repository list. Nothing is discovered.
- Public repositories, including Docker Official Images, without owning them.
- Profiles for metadata, pull statistics, tags, digests, architectures, image details, and staleness.
- Profile inheritance. An explicit false overrides an inherited true.
- A local SQLite cache, so lists and reports work offline.
- Human tables, JSON, CSV, and Markdown.
- A terminal interface for the same cached view.
- An optional token from `--token` or `DOCKERHUB_TOKEN`. The command-line value wins. It is not stored.

## Installation

Install from a checkout while the first release is in progress.

```bash
make init
make python-install-dev
```

Verify the command.

```bash
dockerhub-intel --version
```

## Documentation

Online documentation:

[https://dockerhub-intel.thelupaxaproject.org/](https://dockerhub-intel.thelupaxaproject.org/)

Serve the docs locally:

```bash
make init
make python-install-dev
make mkdocs-serve
```

## Development

Lint, type-check, and test:

```bash
make python-check
```

<a href="https://github.com/the-lupaxa-project">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/components/footer-for-child-orgs.svg" alt="The Lupaxa Project Footer" width="100%" />
</a>
