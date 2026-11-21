'use strict';
// Executed inside the installed n8n image. Secrets arrive only through stdin.
const {createRequire} = require('node:module');
const root = createRequire('/usr/local/lib/node_modules/n8n/package.json');
const nodes = createRequire(root.resolve('n8n-nodes-base/package.json'));
const {NodeSSH} = nodes('node-ssh');
const quote = s => "'" + s.replace(/'/g, "'\\''") + "'";
async function setup(payload, SSH = NodeSSH) {
  const c = payload.credential;
  if (c.username !== 'root' || !/^[0-9a-fA-F:.]+$/.test(c.host) || !/^ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?$/.test(payload.public_key)) throw new Error('Invalid setup');
  const expected = "case \"$SSH_ORIGINAL_COMMAND\" in status) cat /proc/sys/kernel/random/boot_id;; reboot) flock -n /var/lock/n8n-weekly-apt.lock sh -c 'test ! -e /run/n8n-reboot-pending && touch /run/n8n-reboot-pending && /sbin/shutdown -r now';; *) exit 126;; esac";
  if (payload.forced_command !== expected) throw new Error('Invalid restricted command');
  let hostKey = '';
  const ssh = new SSH();
  let stage = 'SSH_CONNECT';
  try {
    await ssh.connect({host:c.host, port:c.port || 22, username:'root', password:c.password,
      readyTimeout:15000,
      hostVerifier(key) {
        const type = key.subarray(4, 4 + key.readUInt32BE(0)).toString();
        hostKey = type + ' ' + key.toString('base64');
        const matches = !payload.expected_key || payload.expected_key === hostKey;
        if (!matches) stage = 'SSH_HOST_KEY';
        return matches;
      }});
    stage = 'SSH_ROOT_CHECK';
    const check = await ssh.execCommand('id -u');
    if (check.code !== 0 || check.stdout.trim() !== '0' || !hostKey) throw new Error('Root/key check failed');
    const line = 'restrict,command=' + JSON.stringify(expected) + ' ' + payload.public_key;
    const identity = payload.public_key.split(' ').slice(0,2).join(' ');
    const command = "umask 077; install -d -m 700 /root/.ssh && touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && tmp=$(mktemp /root/.ssh/authorized_keys.XXXXXX) && { grep -vF -- " + quote(identity) + " /root/.ssh/authorized_keys > \"$tmp\"; code=$?; [ \"$code\" -le 1 ]; } && printf '\\n%s\\n' " + quote(line) + " >> \"$tmp\" && chmod 600 \"$tmp\" && mv \"$tmp\" /root/.ssh/authorized_keys";
    stage = 'SSH_KEY_INSTALL';
    const result = await ssh.execCommand(command);
    if (result.code !== 0) throw new Error('Key installation failed');
    return {host_key:hostKey};
  } catch (error) {
    if (stage === 'SSH_CONNECT' && error.level === 'client-authentication') stage = 'SSH_AUTHENTICATION';
    const safe = new Error(stage);
    safe.setupCode = stage;
    throw safe;
  } finally { ssh.dispose(); }
}
module.exports = {setup};
if (require.main === module || process.argv[1] === undefined) {
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', x => {input += x; if (input.length > 65536) process.exit(1);});
  process.stdin.on('end', () => Promise.resolve().then(() => setup(JSON.parse(input)))
    .then(x => process.stdout.write(JSON.stringify(x)))
    .catch(error => {
      process.stdout.write(JSON.stringify({setup_error:error.setupCode || 'SSH_INPUT'}));
      process.exitCode=1;
    }));
}
