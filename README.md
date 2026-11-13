# n8n on Ubuntu 24.04

A self-hosted n8n deployment with PostgreSQL, an `update-live` command and a private school-email integration for iCloud Calendar.

## What is included

- A Docker Compose deployment, London timezone and persistent encrypted n8n credentials.
- The standard n8n web UI at `https://YOUR_PUBLIC_IPV4` or your DNS hostname, through Caddy. Public-IP mode explicitly requests short-lived publicly trusted Let's Encrypt certificates and renews them automatically. SSH tunnel mode remains available.
- A server-side iCloud wizard: app-specific password, calendar discovery, explicit selection and read-only calendar/mail connection checks.
- An importable workflow that listens for incoming iCloud Mail forwarded to a dedicated alias. No Microsoft/Entra app registration is required.
- AI extraction of multiple event titles, dates, start/end times, locations and useful notes.
- RSVP deadlines go into notes; missing end times use a 60-minute default. Ambiguous dates, cancellations and changes require review.
- SQLite tracking of processed email content and event UIDs to reduce duplicate events.
- Backups before updates; secrets, database and edited n8n workflows are kept outside Git.

This is deployment code and a prepared workflow, not an already-connected Apple/Microsoft account. You supply credentials on your server. No credentials belong in GitHub or chat.

## Install

Run on **Ubuntu 24.04** over SSH:

```bash
curl -fsSL https://raw.githubusercontent.com/waqaarhussain/n8n/main/bootstrap.sh -o /tmp/n8n-bootstrap.sh
sudo bash /tmp/n8n-bootstrap.sh
```

The installer clones to `/opt/waqaar-n8n`, installs Ubuntu's Docker packages if Docker is absent, and asks for:

1. **Your server public IPv4 address**, or a hostname such as `n8n.example.com`. The web UI opens at `https://YOUR_IP` with no port suffix. Ports 80/443 must be reachable from the internet and free for the bundled HTTPS proxy. Public-IP certificate issuance uses HTTP validation on port 80. For a hostname, DNS must point to this server. Leave blank for an SSH tunnel instead. The installer does not modify your firewall.
2. **Apple Account email and an app-specific password**. Generate one at https://account.apple.com/ under Sign-In and Security → App-Specific Passwords. Apple two-factor authentication must be enabled. Do not use your normal Apple password.
3. **An iCloud calendar**, selected from the discovered list. Create a separate `School` calendar in Apple Calendar first if wanted. No new calendar is silently created and no default-calendar fallback is used.
4. **An OpenAI API key**, used for event extraction. ChatGPT Plus does not include API billing. Blank leaves extraction unconfigured. The default model is `gpt-4.1-mini` and can be changed during setup.
5. **iCloud Mail primary address and forwarding alias**. The wizard checks IMAP login without changing messages. An alias shares the primary mailbox; use the primary iCloud Mail address to authenticate. iCloud Mail can remain disabled in the phone Mail app.

The setup wizard lists calendars but does not write a test event. OpenAI API key prompts show a masked first/last-character confirmation after entry; Apple passwords remain hidden. Short keys are masked more heavily.

For local access, run this **on your computer**, keeping SSH open:

```bash
ssh -L 5678:127.0.0.1:5678 YOUR_SSH_USER@YOUR_SERVER
```

Then open http://localhost:5678. With a configured public IP or hostname, use the `https://...` URL printed by the installer. Certificate issuance may take a short time; inspect `docker compose logs caddy` if HTTPS is unavailable. Do not bypass certificate warnings.

Create your n8n owner account in the browser. This is n8n's normal account setup; no shared/default editor password is installed.

If another service already uses ports 80/443, do not start a second proxy. Use localhost mode initially, then configure your existing HTTPS proxy and update `.env` (`N8N_HOST`, `N8N_PROTOCOL=https`, `N8N_PUBLIC_URL`, `N8N_PROXY_HOPS=1`). Keep `N8N_BIND=127.0.0.1`. See `docs/reverse-proxy.md`.

## Forward school email to your iCloud alias

The default now uses **iCloud Mail**, avoiding Microsoft Entra/Azure app registration entirely.

An iCloud email alias is another address for the same mailbox, not a separate account. Leave iCloud Mail disabled in your phone Mail app if you do not want it displayed there. The Ubuntu server connects directly to Apple's IMAP service. Keep the alias active in iCloud and confirm it receives a test email through iCloud.com Mail.

### Outlook forwarding rule

In Outlook.com on the web, open **Settings → Mail → Rules** and create a rule matching your existing school-message criteria. Add a **Forward to** action addressed to your automation alias. Keep the existing action that moves the mail to **Shrubbery**, if wanted.

The rule must act on new arrivals, not depend on somebody manually moving a message later. A server-side rule runs even when Outlook on the phone is closed. Test one new message to ensure it reaches iCloud. Do not set the entire Hotmail mailbox to forward.

### Import and configure

During the server setup wizard, enter:

- Your **primary iCloud Mail address** as the IMAP user. The automation alias is not the login.
- An Apple app-specific password. The wizard can reuse the calendar app-specific password when it is the same Apple Account.
- The **automation alias** that receives school forwards.

The wizard verifies `imap.mail.me.com`, port `993`, TLS, and read-only access to `INBOX`. It does not enable Mail on your phone.

After creating your n8n owner account in the browser:

```bash
sudo n8n-live import
```

This imports your configured IMAP credential into n8n's encrypted credential store and substitutes the saved alias into the workflow. Temporary credential import files are removed afterwards. The workflow is **inactive** until you review and publish it. No email addresses or secrets from your local setup are committed to this public repository.

Open **School email via iCloud alias → iCloud Calendar** and confirm:

- Credential: **School iCloud IMAP**.
- Mailbox: **INBOX**.
- Format: **Simple**. For this IMAP node, Simple includes the full body; it is different from Outlook's preview-only Simplified output.
- Action: **Nothing**, so the trigger does not mark messages read.
- Custom Email Rules: `['ALL', ['TO', 'your-automation-alias@icloud.com']]` using valid JSON with double quotes. The import command fills this in automatically.
- Fetch Only New Emails is enabled.

Both the trigger's IMAP filter and the bridge check the intended recipient address. Unrelated mail is ignored before AI extraction, calendar writes. This is recipient scoping, not a sender-authentication mechanism: anyone knowing the alias can send to it, so keep the address private. Forward normally with the alias in To, not Bcc, so both filters can match it.

The IMAP trigger listens for mailbox arrivals. Delivery depends on Outlook forwarding and iCloud, so do not treat it as guaranteed instant delivery. Initial activation can include matching emails from earlier that day. Test with a disposable email first. PDF, DOCX and DOC attachments are read when using the updated iCloud workflow.

The HTTP Request node calls the private `school-bridge` Docker service. It requires no community nodes or public webhook. The bridge is not exposed on a host port.

A `review` result is routed to a failed execution with a reason. Inspect **Executions**. A review result is cached: resending the same content does not reinterpret it automatically.

If the earlier direct-Outlook workflow was imported, deactivate it before publishing the iCloud one. Turn off the old IFTTT applet after the new route passes its live test to avoid duplicate processing across systems. The optional `workflows/school-outlook.json` template remains available for people who have configured Microsoft OAuth, but it is no longer the default.

## Updates and commands

```bash
sudo update-live                 # where no other update-live existed
sudo n8n-live update              # always works for this project
sudo n8n-setup                    # reconfigure iCloud and AI
sudo n8n-live check-icloud        # read-only connection check
sudo n8n-live web                 # change web UI IP/hostname without replacing credentials
sudo n8n-live status
sudo n8n-live logs
sudo n8n-live backup
sudo n8n-live upgrade-engine      # explicitly pull/pin the current n8n stable image
```

An existing unrelated `/usr/local/bin/update-live` is preserved; it is not overwritten. Updates use a fast-forward Git pull and rebuild the bridge. They do not reimport workflow templates over your edits. Re-running `import` updates the template workflow, so do not do this casually after editing it.

The n8n engine is pinned to the image digest resolved during installation. Normal `update-live` does not jump engine versions. `upgrade-engine` makes a backup and explicitly selects the then-current stable digest. PostgreSQL stays on major version 16.

Local files:

- `.env`: generated database password, n8n encryption key and deployment settings. Root-readable only.
- `config/settings.json`: Apple app-specific password, selected calendar, AI key. Root-owned, readable by the bridge container's group only.
- `data/state.sqlite3`: processed-content fingerprints, extracted event data. Back up this file to preserve duplicate protection.
- `backups/`: private snapshots including secrets, n8n data, database dump and Git revision. Copy backups off-server securely. Automatic retention/deletion is not enabled.

## Reliability and limits

- The AI model runs remotely; this server coordinates API calls. The email body is sent to the configured OpenAI API. It is not uploaded to this repository.
- Matching duplicate email content uses the cleaned subject/body. Changed content can legitimately be processed again. Event deduplication uses a normalised title plus start date/time. This is not a universal semantic duplicate detector and does not deduplicate existing IFTTT events.
- Deterministic CalDAV UIDs and resource URLs make retries reuse the same event resource after an interrupted write.
- Mail trigger cursors can advance before downstream failures. A failed execution needs a deliberate retry from n8n; retries are protected by the content/event state. If email arrival triggers are mission-critical, add a scheduled reconciliation workflow before relying on this unattended.
- iCloud connection checks verify credentials and calendar discovery, not write permission. A shared read-only calendar will fail on the first write. Select an owned/writable calendar.
- The system does not automatically update cancellations/reschedules.
- AI output is validated for schema, date ordering, London offsets and an evidence excerpt present in the source. It can still misunderstand an email. Review the first live events.

## Restore

Stop `n8n` and `school-bridge` before restoring. Use the **matching** `.env`/encryption key, `config`, `data`, n8n volume archive and PostgreSQL dump from one backup. Do not merge a new encryption key with old credentials. See `docs/restore.md` for commands. Restore can overwrite current data; take a fresh backup first.

## Checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r bridge/requirements.txt pytest
.venv/bin/python -m pytest -q
bash -n bootstrap.sh install.sh scripts/n8n-live
```

Tests cover forwarding-header cleanup, complete Outlook bodies, event timing, London offsets, duplicate emails/events, calendar selection, alias scoping and workflow defaults. CI also validates Compose and builds the bridge image.

The prepared code has local automated tests. Ubuntu deployment, iCloud Mail login, iCloud writes must be verified on the actual server/accounts.



## Existing installations: calendar-only update

```bash
sudo n8n-live update
sudo n8n-live remove-whatsapp
```

The second command removes saved InOut recipient keys from the current settings file without changing Apple credentials, the selected calendar, the OpenAI key/model or the mail alias. Normal subsequent updates perform this cleanup too. Older private backups retain their original settings. InOut sending is absent from the application even if older settings are restored. TextMeBot is available separately as an opt-in integration.

Do not reimport the workflow to apply this update: the already-published workflow calls the updated bridge and remains compatible. Its old display name may still mention WhatsApp; rename it in the editor if wanted. No email notifications are sent unless optional TextMeBot is explicitly configured and enabled. Emails with no events finish without creating calendar entries; ambiguous event details require review. Existing email and event duplicate protection is preserved.

## Optional TextMeBot trial

InOut is removed. TextMeBot is a separate opt-in integration. Complete the add-number and WhatsApp-linking steps in the email from TextMeBot first. Its documented endpoint is `https://api.textmebot.com/send.php` with `recipient`, `apikey` and `text` parameters.

```bash
sudo n8n-live update
sudo n8n-live setup-textmebot
sudo n8n-live check-textmebot
# Only after seeing the test message on your phone:
sudo n8n-live enable-textmebot
```

Setup prompts only for the TextMeBot key (masked confirmation) and recipient phone number with country code, preserving Apple and OpenAI settings. Automatic messages stay disabled until explicitly enabled. The check command sends one short test to the saved recipient. Provider HTTP responses are not delivery receipts. No live handset delivery or provider message limits are assumed from unit tests.

Once enabled, messages contain `Email From School`, the subject and the body with recognised school signature/disclaimer blocks removed. Newlines and full URLs are preserved in requests. Long text is split at up to 3,000 characters per part; actual provider acceptance and rendering require testing. Calendar writes finish before notification sending; notification errors are reported separately and cannot undo saved events. Ambiguous sends are held instead of retried automatically. Existing imported workflows remain compatible.

`sudo n8n-live remove-whatsapp` now removes both old InOut and optional TextMeBot settings. Normal updates remove only obsolete InOut keys and preserve TextMeBot configuration. Your private older backups can still contain the original saved keys.

## Document attachments and cleaner calendar notes

The iCloud workflow now downloads attachments and uses a Code node to transfer actual binary buffers to the private bridge. Supported documents: PDF, DOCX and legacy DOC. Text extraction is local; scanned PDF pages use English Tesseract OCR. Extracted document text is sent to the configured OpenAI API along with the email subject/body. Image attachments such as signature logos are ignored. Image-only Word documents need manual review. Attachments are processed in temporary directories and removed after processing; n8n execution storage can still retain input data.

Limits: 10 documents, 10 MiB each, 20 MiB combined, 30 pages per PDF, 90 seconds combined document processing with at most 45 seconds per document, and 120,000 extracted document characters. Protected, corrupt, unsupported or unreadable documents require review and prevent automatic calendar writes for that email. OCR can misread text; uncertain dates remain subject to event validation/review.

For an existing installation, update the server and reimport the workflow to obtain attachment downloading and the preparation node. This deliberately replaces the task-owned template workflow; record any personal node edits first. Stop/unpublish the old workflow in the editor before importing, then review and publish the imported version again. Do not manually execute old cached test data to test attachments.

```bash
sudo n8n-live update
sudo n8n-live import
```

Assumptions and missing-detail commentary are no longer appended to calendar notes. The structured assumptions stay internal for validation; the default duration remains 60 minutes. Reforwarding an existing message can refresh that event's notes at the same stable CalDAV URL, without creating a second event. Outputs distinguish `events_created` and `events_updated`.

## Overview statistics while testing

```bash
sudo n8n-live stats-reset  # numbered workflow picker; 0 resets all workflow overview counters
sudo n8n-live stats        # numbered workflow picker; toggles that workflow's stats pause/resume
```

The reset backs up Insights tables and briefly stops/restarts n8n so buffered counts are flushed before clearing. It deletes only the selected overview-counter data, not execution logs, workflows, credentials, calendar events or bridge duplicate tracking. Refresh the Overview page afterwards.

The pause helper keeps workflows running and execution history available. It installs a local PostgreSQL trigger on `insights_raw` that zeroes metric values for the selected workflow while paused. Time windows also exclude buffered records flushed after resuming. Counts from before pausing remain. A run is classified by its completion/Insights timestamp. This is a local extension to n8n, not a native n8n feature; schema guards reject incompatible versions. It has been checked against n8n 2.47.5 source and tested with raw-to-counter data in PostgreSQL. Recheck it before upgrading n8n across database schema changes.

## Helper menu

Run `helper` for a numbered menu with a description of every n8n maintenance command. Selecting a command continues its existing flow, including the workflow picker for stats. The selected helper reacquires the maintenance lock normally; menu dispatch releases its own lock first. This menu covers this n8n project, not unrelated applications on your server.

For an installation predating the menu, run `sudo n8n-live update`, then `sudo n8n-live helper` once. That first menu invocation also installs the `helper` shortcut. If a different application already owns `/usr/local/bin/helper`, it is preserved; use `sudo n8n-live helper` for this menu.
