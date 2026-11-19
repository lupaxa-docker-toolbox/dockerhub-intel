# Docker Hub Intel

`dockerhub-intel` monitors an explicit list of Docker Hub repositories. It stores the results in a local cache and can show them without a network connection. Groups organise the list. Profiles decide which checks run.

The application is read-only. It does not publish images, delete tags, or change repository settings. It does not need to own a repository to read public information.

## Behaviour

- The repository list is configuration, not a search of Docker Hub.
- A short official name such as `nginx` is stored as `library/nginx`.
- Profiles turn checks on or off. A repository without a profile uses `defaults.profile`.
- Synchronisation reads Docker Hub and writes the cache. Listing, reports, and the terminal interface read the cache.
- One repository failure does not cancel the others.
- An optional token comes from `--token` or `DOCKERHUB_TOKEN`. The command-line value wins. It is not written to configuration, the database, or logs.

## Requirements

- Python 3.11 or later.
- `--token` or `DOCKERHUB_TOKEN` only when a repository is private or an endpoint requires authentication.
- Public repositories can be read without a token, subject to Docker Hub rate limits.
