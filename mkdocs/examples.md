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

display:
  date_format: DD/MM/YYYY HH:mm UTC

sync:
  concurrency: 10
  timeout: 15
  retries: 3

history:
  enabled: true
  retention_days: 0
```

`retention_days: 0` keeps every snapshot.

`display.date_format` is how Updated is shown in the list, the repository detail, tag tables, and statistics.
`YYYY`, `MM`, and `DD` are each required once.
Leave out `HH` and `mm` to show the date only.
The first `MM` is the month.
When the clock is included, the second `MM` is the minute.
`ss` and `UTC` are optional with the time.
A stored value that has no time shows `--` for a clock the pattern asks for.
JSON, CSV, and Markdown use the same text.
