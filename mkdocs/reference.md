# Reference

## Identity

| Item            | Value                      |
| --------------- | -------------------------- |
| Command         | `dockerhub-intel`          |
| Distribution    | `lupaxa-dockerhub-intel`   |
| Import package  | `lupaxa.dockerhub_intel`   |
| Default profile | `standard`                 |
| Ungrouped name  | `ungrouped`                |

## Exit Codes

| Code | Meaning                                                                              |
| ---- | ------------------------------------------------------------------------------------ |
| 0    | Success                                                                              |
| 1    | General application error                                                            |
| 2    | Invalid command or configuration                                                     |
| 3    | Authentication or authorisation failure                                              |
| 4    | Synchronisation incomplete                                                           |
| 5    | Requested repository not found                                                       |
| 6    | Monitoring condition detected when an explicit failure option is enabled             |

## Cache Freshness

| Data                        | Default TTL |
| --------------------------- | ----------- |
| Repository metadata         | 60 minutes  |
| Pull statistics             | 60 minutes  |
| Tags                        | 60 minutes  |
| Manifests and architectures | 6 hours     |

Expired rows stay readable until the next successful refresh or an explicit purge. History is kept indefinitely unless `history.retention_days` is set above zero. Cleanup never removes the latest row needed to browse offline.

## Commands

| Command            | Purpose                                                      |
| ------------------ | ------------------------------------------------------------ |
| `list`             | List configured repositories and cached values               |
| `repo list`        | List configured repositories and cached values               |
| `repo add`         | Add a repository to the local list                           |
| `repo remove`      | Remove a repository from the local list                      |
| `repo show`        | Show one repository, its growth, and its tags                |
| `repo enable`      | Include a repository in synchronisation                      |
| `repo disable`     | Skip a repository during synchronisation                     |
| `repo set-group`   | Change the group stored for a repository                     |
| `repo set-profile` | Change the profile stored for a repository                   |
| `profile list`     | List profiles and the profile each one extends               |
| `profile show`     | Show the resolved checks for one profile                     |
| `profile tree`     | Show each profile and its parent                             |
| `profile validate` | Validate profile inheritance                                 |
| `config show`      | Show effective settings. The token is omitted                |
| `config validate`  | Validate the configuration files                             |
| `config paths`     | Show the configuration and database directories              |
| `sync`             | Refresh selected repositories from Docker Hub                |
| `stats`            | Show cached pull counts and observed growth                  |
| `tags`             | Show cached image tags                                       |
| `digests`          | Show cached tag digests                                      |
| `architectures`    | Show cached image platforms                                  |
| `changes`          | Show stored tag and digest changes                           |
| `cache info`       | Show how many rows are stored                                |
| `cache clear`      | Delete cached rows. Configuration is kept                    |
| `cache vacuum`     | Reclaim free pages in the database                           |
| `report`           | Write a repository report from the cache                     |
| `tui`              | Open the terminal dashboard                                  |
| `doctor`           | Check configuration, the database, and whether a token is set|

With no arguments, a terminal opens the dashboard. `list` prints the repository table. Both exit without an error when no repositories are configured.

The dashboard has a search box. `/` focuses it. Enter opens a repository. `r` synchronises, and Escape cancels that synchronisation. `p` lists profiles and `c` shows configuration paths. Control+Q quits.

`--config`, `--offline`, `--verbose`, `--debug`, and `--token` apply to every command. `--format` accepts `text`, `json`, `csv`, or `markdown`. `--fail-on stale` exits with code 6 when a listed repository is stale.

`sync` draws a progress bar on a terminal. It pauses workers when Docker Hub returns 429 or zero remaining quota; the bar shows the pause length. `--quiet` hides the bar. Hub API and registry limits are tracked separately.

## Read-Only Boundary

The client does not call operations that publish an image, delete a repository or tag, edit a description, change visibility, or change account settings.
