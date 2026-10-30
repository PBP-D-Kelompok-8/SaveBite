# Request board maintenance

## Creation key retention

Idempotent creation retries and deleted-request protection are guaranteed for
30 days from the first successful creation, scoped to the authenticated owner.
Cleanup removes both linked records and deletion tombstones older than 30 days;
it never deletes food requests. At exactly 30 days a key remains retained.
After cleanup, an old UUID is treated as a new key and the status endpoint returns
`found: false`. Clients must not retry old drafts beyond the retention window.
The browser blocks retries 30 days after the first submission attempt, including
direct form submission and retries delayed by a CSRF refresh. It checks both
wall-clock and monotonic elapsed time and keeps expired attempts blocked even
if the clock moves back. Status lookups remain available; a retained committed
key can still confirm success. The draft remains locked when the result cannot
be confirmed, so it cannot silently acquire a new UUID and create a duplicate.

Run migrations before deploying this release to add the cleanup timestamp index:

```sh
python manage.py migrate
python manage.py cleanup_request_creation_keys --dry-run
```

Configure a daily scheduled job in the deployment environment, using the same
database credentials and production settings as the web process. For example,
this cron entry uses absolute paths that must be replaced for the deployment:

```cron
0 3 * * * cd /srv/savebite && /srv/savebite/.venv/bin/python manage.py cleanup_request_creation_keys >> /var/log/savebite-key-cleanup.log 2>&1
```

The command deletes in batches of 500 and can safely be rerun. Monitor failures
and its deletion count. A missed run extends retention rather than removing keys
early. This repository does not configure the deployment host's scheduler;
the job must be installed there as part of rollout.

## Shared asset releases

All three request board pages use `board_static` for their shared JavaScript
and request board stylesheets. Update `ASSET_VERSION` in
`templatetags/request_board_assets.py` whenever releasing changes to those assets.
