'use strict';
// Uses the real node-ssh package inside the official n8n image; no network calls.
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const source = require('node:fs').readFileSync('/scripts/vps-ssh-setup.cjs', 'utf8');
const {setup} = require('/scripts/vps-ssh-setup.cjs');
const {createRequire} = require('node:module');
const root=createRequire('/usr/local/lib/node_modules/n8n/package.json');
const native=createRequire(root.resolve('n8n-nodes-base/package.json'));
assert.equal(typeof native('node-ssh').NodeSSH, 'function');
const type=Buffer.from('ssh-ed25519');
const size=Buffer.alloc(4);size.writeUInt32BE(type.length);
const hostKey=Buffer.concat([size,type,Buffer.from('fake-key')]);
const commands=[];
class SSH {
  async connect(options) {assert(options.hostVerifier(hostKey));}
  async execCommand(command) {commands.push(command);return {code:0,stdout:command==='id -u'?'0':''};}
  dispose() {}
}
const forced="case \"$SSH_ORIGINAL_COMMAND\" in status) cat /proc/sys/kernel/random/boot_id;; reboot) flock -n /var/lock/n8n-weekly-apt.lock sh -c 'test ! -e /run/n8n-reboot-pending && touch /run/n8n-reboot-pending && /sbin/shutdown -r now';; *) exit 126;; esac";
(async () => {
  const payload={credential:{host:'192.0.2.1',username:'root',password:'never-in-command'},public_key:'ssh-ed25519 YWJj n8n-reboot-only',forced_command:forced};
  // Production has an IMAP startup preload that logs to stdout. Reproduce the
  // JSON corruption, then verify the helper's isolated NODE_OPTIONS override.
  const inherited = {...process.env, NODE_OPTIONS:'--require=/engine/fast-mail.cjs', N8N_FAST_MAIL_TEST:'0'};
  const noisy = spawnSync(process.execPath, ['-e', source], {input:'{invalid', encoding:'utf8', env:inherited});
  assert.equal(noisy.status, 1);
  assert(noisy.stdout.includes('Fast mail enabled:'));
  assert.throws(() => JSON.parse(noisy.stdout));
  const cleanEnv = {...inherited, NODE_OPTIONS:''};
  // Exercise the actual node -e/stdin entry point, not just the imported function.
  const invalid = spawnSync(process.execPath, ['-e', source], {input:'{invalid', encoding:'utf8', env:cleanEnv});
  assert.equal(invalid.status, 1);
  assert.deepEqual(JSON.parse(invalid.stdout), {setup_error:'SSH_INPUT'});
  assert.equal(invalid.stderr, '');
  const refused = spawnSync(process.execPath, ['-e', source], {
    input:JSON.stringify({...payload, credential:{...payload.credential, host:'127.0.0.1', port:1}}),
    encoding:'utf8', timeout:20000, env:cleanEnv
  });
  assert.equal(refused.status, 1);
  assert.deepEqual(JSON.parse(refused.stdout), {setup_error:'SSH_CONNECT'});
  assert(!refused.stdout.includes('never-in-command'));
  const result=await setup(payload,SSH);
  assert(result.host_key.startsWith('ssh-ed25519 '));
  assert(commands[1].includes('restrict,command='));
  assert(commands[1].includes('authorized_keys'));
  assert(!commands.join().includes('never-in-command'));
  // Replace the managed legacy key atomically, preserving unrelated access.
  const fs=require('node:fs');
  const path=require('node:path');
  const dir=fs.mkdtempSync('/tmp/reboot-key-test-');
  try {
    const file=path.join(dir,'authorized_keys');
    fs.writeFileSync(file,'ssh-ed25519 b3RoZXI other-user\nrestrict,command="old" '+payload.public_key+'\n');
    const install=commands[1].replaceAll('/root/.ssh',dir);
    for (let i=0;i<2;i++) {
      const applied=spawnSync('/bin/sh',['-c',install],{encoding:'utf8'});
      assert.equal(applied.status,0,applied.stderr);
    }
    const lines=fs.readFileSync(file,'utf8').trim().split('\n').filter(Boolean);
    assert.equal(lines.length,2);
    assert(lines.includes('ssh-ed25519 b3RoZXI other-user'));
    assert.equal(lines.filter(line=>line.includes(payload.public_key)).length,1);
    assert(!lines.some(line=>line.includes('command="old"')));
    assert(lines.some(line=>line.includes('shutdown -r now')));
  } finally {fs.rmSync(dir,{recursive:true,force:true});}

  await assert.rejects(setup({...payload,expected_key:'ssh-ed25519 different'},SSH));
  await assert.rejects(setup({...payload,forced_command:'reboot now'},SSH));
  const wf=require('/tmp/vps-generated.json');
  const results=wf.nodes.filter(n=>n.name.startsWith('Result '));
  assert.equal(results.length,3);
  const outputs=results.map((n,i)=>new Function('$input',n.parameters.jsCode)({first:()=>({json:{code:i===1?100:0,stdout:'N8N_REBOOT_REQUIRED=1'}})})[0]);
  assert.equal(outputs[0].json.update_status,'success');
  assert.equal(outputs[1].json.update_status,'failed');
  assert.equal(outputs[2].json.update_status,'success');
  const summary=wf.nodes.find(n=>n.name==='VPS reboot summary');
  const final=new Function('$',summary.parameters.jsCode)(name=>({first:()=>outputs[results.findIndex(n=>n.name===name)]}));
  assert.equal(final.length,3);
  process.stdout.write(root('flatted').stringify({resultData:{runData:{'VPS reboot summary':[{data:{main:[final]}}]}}}));
})().catch(error=>{console.error(error);process.exitCode=1;});
