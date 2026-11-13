const items = $input.all();
const result = [];
for (let i = 0; i < items.length; i++) {
  const attachments = [];
  const issues = [];
  let total = 0;
  for (const [key, file] of Object.entries(items[i].binary || {})) {
    const filename = file.fileName || key;
    const mime = file.mimeType || '';
    if (mime.startsWith('image/')) continue; // inline signatures/logos
    if (!/\.(pdf|docx?)$/i.test(filename)) {
      issues.push('Unsupported attachment: ' + filename + '. Review manually.');
      continue;
    }
    if (attachments.length >= 10) { issues.push('More than 10 documents; review manually.'); break; }
    const buffer = await this.helpers.getBinaryDataBuffer(i, key);
    total += buffer.length;
    if (buffer.length > 10 * 1024 * 1024 || total > 20 * 1024 * 1024) {
      issues.push('Document size limit exceeded; review manually.'); break;
    }
    attachments.push({filename, mimeType: mime, data: buffer.toString('base64')});
  }
  result.push({json: {...items[i].json, attachments, attachment_issues: issues}, pairedItem: {item: i}});
}
return result;
