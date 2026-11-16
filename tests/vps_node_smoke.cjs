'use strict';
// Pinned official n8n image. Mock SSH transport: never opens a socket or upgrades a host.
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const root = createRequire('/usr/local/lib/node_modules/n8n/package.json');
const nodesPath = root.resolve('n8n-nodes-base/package.json');
const nodes = createRequire(nodesPath);
const path = require('node:path');
const {Ssh} = nodes(path.join(path.dirname(nodesPath), 'dist/nodes/Ssh/Ssh.node.js'));
const {NodeSSH} = nodes('node-ssh');
const {stringify} = root('flatted');
const workflow = require('/workflows/vps-weekly-updates.json');
const config = workflow.nodes[3].parameters;
let connected = false;
NodeSSH.prototype.connect = async function(credentials) {
  assert.equal(credentials.username, 'root');
  assert.equal(credentials.host, '192.0.2.1');
  connected = true;
};
NodeSSH.prototype.execCommand = async function(command) {
  assert.equal(command, config.command);
  return {code: 0, stdout: 'packages updated\nN8N_REBOOT_REQUIRED=1', stderr: ''};
};
NodeSSH.prototype.dispose = function() {};
(async () => {
  const ssh = new Ssh();
  assert(ssh.description.credentials.some(c => c.name === 'sshPassword'));
  const output = await ssh.execute.call({
    getInputData: () => [{json:{}}],
    getNodeParameter: key => config[key],
    getCredentials: async type => {
      assert.equal(type, 'sshPassword');
      return {host:'192.0.2.1', port:22, username:'root', password:'dummy'};
    },
    continueOnFail: () => false,
  });
  assert(connected);
  const summary = new Function('$input', '$', workflow.nodes[4].parameters.jsCode)(
    {first: () => output[0][0]}, () => ({first: () => ({json:{vps_name:'Stored VPS name'}})})
  );
  const graph = {resultData:{runData:{'VPS reboot summary':[{data:{main:[summary]}}]}}};
  process.stdout.write(stringify(graph));
})().catch(e => {console.error(e); process.exitCode=1;});
