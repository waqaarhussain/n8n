# Restore a matching backup

Run as root from `/opt/waqaar-n8n`. Take a fresh `n8n-live backup` before replacing anything. Replace the example backup path with the snapshot to restore. These commands overwrite current application data.

```bash
sudo -i
cd /opt/waqaar-n8n
restore_dir=/opt/waqaar-n8n/backups/20261112T120000Z
# Verify all required files exist BEFORE stopping/replacing anything.
for required in env n8n.dump n8n-data.tgz config/settings.json data/state.sqlite3 commit; do
  test -f "$restore_dir/$required" || { echo "Missing $required"; exit 1; }
done
docker compose stop n8n school-bridge
cp "$restore_dir/env" .env
chmod 600 .env
cp "$restore_dir/config/settings.json" config/settings.json
chown root:1000 config/settings.json
chmod 640 config/settings.json
# Keep the old tracking DB together with its WAL files for recovery.
mv data "data-before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$restore_dir/data" data
chown -R 1000:1000 data
# Use the saved Git revision if compatibility with older bridge code is required.
docker compose up -d postgres
docker compose exec -T postgres pg_restore -U n8n -d n8n --clean --if-exists --no-owner < "$restore_dir/n8n.dump"
docker compose run --rm --no-deps --user 0 --entrypoint sh -v "$restore_dir:/restore:ro" n8n -c 'find /home/node/.n8n -mindepth 1 -maxdepth 1 -exec rm -rf {} +; tar xzf /restore/n8n-data.tgz -C /home/node/.n8n; chown -R 1000:1000 /home/node/.n8n'
docker compose build school-bridge
docker compose up -d --wait --wait-timeout 180
n8n-live check-icloud
```

If PostgreSQL's current role password differs from the restored `.env`, restore the matching password in PostgreSQL before starting n8n. Reusing the existing PostgreSQL volume does not rerun its initial password setup. Use PostgreSQL's interactive `\password n8n` command; do not paste the password into shell history.

Restoring tracking data from an older backup can lose records of previously processed emails and events. Check calendar entries before publishing or retrying.
