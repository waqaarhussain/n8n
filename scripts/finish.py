from pathlib import Path
values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line)
print('\nInstalled. Open ' + values['N8N_PUBLIC_URL'] + ' and create your n8n owner account.')
if values['N8N_PROTOCOL'] == 'http':
    print('From your computer: ssh -L 5678:127.0.0.1:5678 YOUR_SSH_USER@YOUR_SERVER')
print('Then run: sudo n8n-live import')
print('Open the imported workflow, connect Outlook and select ONLY the Shrubbery folder.')
print('Configure both folder and read-status filters before publishing. See README.md.')
print('Update: n8n-live update (or update-live if this server had no existing update-live).')
print('Reconfigure iCloud/AI/WhatsApp: n8n-setup')
print('Read-only iCloud check: n8n-live check-icloud')
