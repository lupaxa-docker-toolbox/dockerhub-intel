# Examples

## Profiles

```yaml
profiles:
  base:
    metadata: true
    stats: true
    tags: false
    digests: false
    architectures: false
    image_details: false

  standard:
    extends: base
    tags: true

  detailed:
    extends: standard
    digests: true
    architectures: true
    image_details: true

  watchlist:
    extends: standard
    digests: true
```

`watchlist` resolves through `standard` to `base`. An explicit `false` replaces an inherited `true`.

## Repositories

```yaml
repositories:
  - repo: mydockeruser/my-application
    group: personal
    profile: detailed

  - nginx

  - repo: library/redis
    group: official-images
    profile: watchlist
    checks:
      architectures: true

  - repo: anotherpublisher/experimental-image
    group: third-party
    enabled: false
```

`nginx` is the same repository as `library/nginx`. `enabled: false` keeps the entry and skips it during synchronisation. Cached history for that repository is left in place.

## Application Settings

```yaml
version: 1

dockerhub:
  token_env: DOCKERHUB_TOKEN
  api_url: https://hub.docker.com/v2

files:
  profiles: profiles.yml
  repositories: repositories.yml

defaults:
  profile: standard

sync:
  concurrency: 10
  timeout: 15
  retries: 3

history:
  enabled: true
  retention_days: 0
```

`retention_days: 0` keeps every snapshot.
