import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await mkdir(path.join(root, 'static', 'data'), { recursive: true });
for (const name of ['summary.json','official-results.json','missing-logs.json','untimed-sections.json','reconciliation.json']) {
 const source = path.join(root,'data',name);
 try { await copyFile(source,path.join(root,'static','data',name)); }
 catch (error) { if(name==='summary.json')throw error; }
}
const snapshot = JSON.parse(await readFile(path.join(root,'static','data','summary.json'),'utf8'));
const keys = ['total','before17','between17and19','after19','unclassified'];
for (const row of snapshot.municipalities) {
 if(row.total!==row.before17+row.between17and19+row.after19+row.unclassified)throw new Error('Bucket mismatch: '+row.id);
 if(row.histogram.reduce((a,b)=>a+b,0)+row.unclassified!==row.total)throw new Error('Histogram mismatch: '+row.id);
}
for (const key of keys) {
 const total=snapshot.municipalities.filter((row)=>!row.isOverseas).reduce((sum,row)=>sum+row[key],0);
 if(total!==snapshot.totals[key])throw new Error('Domestic aggregate mismatch: '+key);
}
const official=JSON.parse(await readFile(path.join(root,'static','data','official-results.json'),'utf8'));
if(official.national.totals.totalBallots!==128236074)throw new Error('Unexpected official benchmark');
if(official.national.totals.validVotes+official.national.totals.blankVotes+official.national.totals.nullVotes!==official.national.totals.totalBallots)throw new Error('Official categories do not reconcile');
await writeFile(path.join(root,'static','data','snapshot-version.json'),JSON.stringify({updatedAt:snapshot.updatedAt,domesticBallots:snapshot.totals.total,analyzedSections:snapshot.coverage.analyzedSections,status:snapshot.status}));
console.log('Validated snapshot: '+snapshot.totals.total.toLocaleString('en')+' domestic ballots; '+snapshot.coverage.analyzedSections.toLocaleString('en')+' sections.');
