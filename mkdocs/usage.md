# Usage

Synchronisation reads Docker Hub. Everything else reads the local cache, including when the machine is offline.

## What is Collected

| Check         | Collects                                                         |
| ------------- | ---------------------------------------------------------------- |
| metadata      | Description, visibility, dates, stars, and the Docker Hub URL    |
| stats         | Cumulative pull count, stored as a timestamped snapshot          |
| tags          | Tag names and the changes between complete listings              |
| digests       | The digest behind a tag, and a record when it changes            |
| architectures | Operating system and CPU architecture for an image               |
| image_details | Manifest metadata. Layers are not downloaded                     |
| stale         | Whether a repository or tag is older than a configured threshold |

A profile inherits from one parent with `extends`. A repository `checks` block overrides that profile for one repository only.

## Pull Counts

Docker Hub exposes a repository-level cumulative pull count. Growth is the difference between stored snapshots. The application does not invent a daily download series from a single total.

If a later total is lower than an earlier one, the lower value is kept and marked as an anomaly.

## Tags and Digests

A tag is reported removed only when the new listing finished without error. A partial listing does not delete local tag history.

Digest monitoring compares the top-level manifest or index digest. A change there does not by itself mean every platform image changed.

## Offline

Cached commands do not open a network connection. Stale and missing values stay visible and are labelled as such.
