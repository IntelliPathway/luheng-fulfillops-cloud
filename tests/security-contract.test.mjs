import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import test from 'node:test';

const root=resolve(import.meta.dirname,'..');

test('generates a CycloneDX inventory for frontend and backend dependencies',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'repayguard-sbom-'));
  const output=join(directory,'bom.json');
  try{
    const result=spawnSync(process.execPath,['scripts/generate-sbom.mjs',`--output=${output}`],{cwd:root,encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    const bom=JSON.parse(await readFile(output,'utf8'));
    assert.equal(bom.bomFormat,'CycloneDX');
    assert.equal(bom.metadata.component.version,'2.0.0');
    assert.ok(bom.components.some(item=>item.purl.startsWith('pkg:npm/react@')));
    assert.ok(bom.components.some(item=>item.purl.startsWith('pkg:pypi/fastapi@')));
    assert.equal(JSON.stringify(bom).includes('github_pat_'),false);
  }finally{await rm(directory,{recursive:true,force:true})}
});
