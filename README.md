# n8n on Ubuntu 24.04

A self-hosted n8n deployment with PostgreSQL, an `update-live` command and a private school-email integration for iCloud Calendar and InOut WhatsApp.

## What is included

- A Docker Compose deployment, London timezone and persistent encrypted n8n credentials.
- The standard n8n web UI at `https://YOUR_PUBLIC_IPV4` or your DNS hostname, through Caddy. Public-IP mode explicitly requests short-lived publicly trusted Let's Encrypt certificates and renews them automatically. SSH tunnel mode remains available.
- A server-side iCloud wizard: app-specific password, calendar discovery, explicit selection and read-only calendar/mail connection checks.
- An importable workflow that listens for incoming iCloud Mail forwarded to a dedicated alias. No Microsoft/Entra app registration is required.
- AI extraction of multiple event titles, dates, start/end times, locations and useful notes.
- RSVP deadlines go into notes; missing end times use a clearly labelled 60-minute placeholder. Ambiguous dates, cancellations and changes require review.
- SQLite tracking of processed email content and event UIDs to reduce duplicate events and messages.
- WhatsApp messages headed `Email From School`, followed by a clean subject and the email body. Standard forwarding headers are removed.
- Multiple independently configured InOut recipient keys. Each recipient must connect to InOut themselves.
- Long messages split into numbered parts without locally truncating the body. The conservative default is 350 characters per part because the previous IFTTT/InOut path truncated messages. Provider limits and handset delivery still require a live test. More parts consume more InOut messages.
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
6. **InOut API keys**, one per recipient. Each person sends `Create APIKey` to the InOut WhatsApp bot in their own chat. InOut's allowance/subscription is separate. HTTP acceptance does not prove delivery. Multiple-recipient delivery and provider-added message introductions are not verified until you test them.

The setup wizard lists calendars but does not write a test event or send WhatsApp messages. API key prompts show a masked first/last-character confirmation after entry; Apple passwords remain hidden. Short keys are masked more heavily. InOut identifies your connected WhatsApp number from its API key, so there is no separate recipient phone-number field.

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

Open **School email via iCloud alias → iCloud Calendar + WhatsApp** and confirm:

- Credential: **School iCloud IMAP**.
- Mailbox: **INBOX**.
- Format: **Simple**. For this IMAP node, Simple includes the full body; it is different from Outlook's preview-only Simplified output.
- Action: **Nothing**, so the trigger does not mark messages read.
- Custom Email Rules: `['ALL', ['TO', 'your-automation-alias@icloud.com']]` using valid JSON with double quotes. The import command fills this in automatically.
- Fetch Only New Emails is enabled.

Both the trigger's IMAP filter and the bridge check the intended recipient address. Unrelated mail is ignored before AI extraction, calendar writes and WhatsApp sending. This is recipient scoping, not a sender-authentication mechanism: anyone knowing the alias can send to it, so keep the address private. Forward normally with the alias in To, not Bcc, so both filters can match it.

The IMAP trigger listens for mailbox arrivals. Delivery depends on Outlook forwarding and iCloud, so do not treat it as guaranteed instant delivery. Initial activation can include matching emails from earlier that day. Test with a disposable email first. Attachments are not read.

The HTTP Request node calls the private `school-bridge` Docker service. It requires no community nodes or public webhook. The bridge is not exposed on a host port.

A `review` result or uncertain WhatsApp delivery is routed to a failed execution with a reason. Inspect **Executions**. A review result is cached: resending the same content does not reinterpret it automatically.

If the earlier direct-Outlook workflow was imported, deactivate it before publishing the iCloud one. Turn off the old IFTTT applet after the new route passes its live test to avoid duplicate processing across systems. The optional `workflows/school-outlook.json` template remains available for people who have configured Microsoft OAuth, but it is no longer the default.

## Updates and commands

```bash
sudo update-live                 # where no other update-live existed
sudo n8n-live update              # always works for this project
sudo n8n-setup                    # reconfigure iCloud, AI and WhatsApp
sudo n8n-live check-icloud        # read-only connection check
sudo n8n-live check-whatsapp      # sends one short test per configured recipient
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
- `config/settings.json`: Apple app-specific password, selected calendar, AI key and recipient keys. Root-owned, readable by the bridge container's group only.
- `data/state.sqlite3`: processed-content fingerprints, extracted event data and per-recipient delivery states. Back up this file to preserve duplicate protection.
- `backups/`: private snapshots including secrets, n8n data, database dump and Git revision. Copy backups off-server securely. Automatic retention/deletion is not enabled.

## Reliability and limits

- The AI model runs remotely; this server coordinates API calls. The email body is sent to the configured OpenAI API and to each configured InOut recipient. It is not uploaded to this repository.
- Matching duplicate email content uses the cleaned subject/body. Changed content can legitimately be processed again. Event deduplication uses a normalised title plus start date/time. This is not a universal semantic duplicate detector and does not deduplicate existing IFTTT events.
- Deterministic CalDAV UIDs and resource URLs make retries reuse the same event resource after an interrupted write.
- A WhatsApp timeout is ambiguous: InOut may already have accepted it. The bridge records it as uncertain and does **not** automatically resend that part. Inspect the recipient chat/provider first. No automatic delivery receipts or retry administration UI is included.
- Mail trigger cursors can advance before downstream failures. A failed execution needs a deliberate retry from n8n; retries are protected by the content/event state. If email arrival triggers are mission-critical, add a scheduled reconciliation workflow before relying on this unattended.
- iCloud connection checks verify credentials and calendar discovery, not write permission. A shared read-only calendar will fail on the first write. Select an owned/writable calendar.
- The system does not process attachments, automatically update cancellations/reschedules, send to arbitrary WhatsApp numbers through one key, or remove provider-imposed message templates.
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

Tests cover forwarding-header cleanup, complete Outlook bodies, event timing, London offsets, duplicate emails/events, multi-recipient fan-out, uncertain-send handling, calendar selection, alias scoping and workflow defaults. CI also validates Compose and builds the bridge image.

The prepared code has local automated tests. Ubuntu deployment, iCloud Mail login, iCloud writes and InOut handset delivery must be verified on the actual server/accounts.

WhatsApp diagnostics: processing responses include `whatsapp_errors` with redacted provider errors. Older uncertain sends have no saved error detail. Use `n8n-live check-whatsapp` to send a fresh short test without reprocessing email or creating calendar events. It uses the provider message allowance. A successful request is not a delivery receipt.

Notification cleanup removes standalone forwarding separators, the Shrubbery School Limited signature block and recognised confidentiality footers. This only changes WhatsApp presentation; extraction, calendar evidence and email fingerprints retain the complete cleaned source. Chunks prefer paragraph/sentence boundaries and never split a URL. InOut may still add its own introduction and flatten line breaks.
