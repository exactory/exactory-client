# Maintaining the default-branch Lean cache

The `Maintain Lean cache` workflow runs daily at 04:23 UTC and can be started
manually. Its single Ubuntu 24.04 job runs only on the repository's default
branch. Restoring that branch's cache keeps it available to ordinary CI and
pull requests even when no changes are pushed to `main`.

The workflow uses the same pinned actions, `~/.elan` path and cache key as
`ci.yml`. The key includes the cache epoch, runner OS, architecture and the
smoke fixture's `lean-toolchain` hash. `ci/install_lean.py` probes restored
binaries and repairs an unusable installation with bounded download attempts.
The real Lean smoke fixture must then build successfully. A cache miss is saved
only after these steps succeed. Restore and save each have a 15-minute limit
and tolerate cache service failures; installation and build failures fail the
job. The whole job has a 90-minute limit.

After the workflow reaches the default branch, start it from GitHub Actions by
selecting **Maintain Lean cache**, choosing **Run workflow**, and selecting the
default branch. A dispatch on any other branch skips the job. With GitHub CLI:

```sh
gh workflow run lean-cache.yml --ref main -R exactory/exactory-client
gh run list --workflow lean-cache.yml --branch main -R exactory/exactory-client
gh cache list --ref refs/heads/main -R exactory/exactory-client
```

Open the resulting run and confirm that restore, installation and smoke build
completed. On a healthy cache hit, the installer reports that nothing was
downloaded. On a miss, confirm that save completed too. Cache entries are
immutable; to replace a damaged entry, delete that entry through GitHub's cache
management and dispatch again, or coordinate a cache epoch change in both
workflows.

Scheduled runs may be delayed or dropped, and GitHub disables public-repository
schedules after 60 days without repository activity. Check that the schedule
remains enabled after a long idle period. Maintenance reduces avoidable cold
downloads; it does not guarantee cache retention or network availability.
