import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';

const root=resolve(import.meta.dirname,'..');
const outputArg=process.argv.find(value=>value.startsWith('--output='));
const output=resolve(root,outputArg?.slice(9)||'artifacts/repayguard-ai.cdx.json');
const lock=JSON.parse(await readFile(resolve(root,'package-lock.json'),'utf8'));
const requirements=(await readFile(resolve(root,'backend/requirements.txt'),'utf8')).split('\n').map(line=>line.trim()).filter(line=>line&&!line.startsWith('#'));
const components=[];
for(const [path,entry] of Object.entries(lock.packages||{})){
  if(!path.startsWith('node_modules/')||!entry.version)continue;
  const name=path.slice('node_modules/'.length);
  components.push({type:'library',group:name.startsWith('@')?name.split('/')[0]:'',name:name.startsWith('@')?name.split('/')[1]:name,version:entry.version,purl:`pkg:npm/${encodeURIComponent(name)}@${entry.version}`});
}
for(const requirement of requirements){
  const match=requirement.match(/^([A-Za-z0-9_.-]+)==([^;\s]+)/);
  if(!match)continue;
  components.push({type:'library',name:match[1],version:match[2],purl:`pkg:pypi/${match[1].toLowerCase()}@${match[2]}`});
}
components.sort((a,b)=>a.purl.localeCompare(b.purl));
const bom={bomFormat:'CycloneDX',specVersion:'1.5',version:1,metadata:{component:{type:'application',name:lock.name,version:lock.version}},components};
await mkdir(dirname(output),{recursive:true});
await writeFile(output,`${JSON.stringify(bom,null,2)}\n`);
console.log(output);
