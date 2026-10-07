import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {validateRuntimeConfig} from '../src/runtime-config.js';

test('enterprise bundle matches runtime validation and restricts public-client login',()=>{
 const directory=mkdtempSync(join(tmpdir(),'enterprise-config-'));
 try{
  const args=['scripts/enterprise-config.py','--site-origin','https://app.example.com','--api-origin','https://api.example.com','--identity-origin','https://login.example.com','--output',directory];
  const result=spawnSync('python',args,{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
  const runtime=JSON.parse(readFileSync(join(directory,'runtime-config.json'),'utf8'));assert.equal(validateRuntimeConfig(runtime).mode,'connected');
  const realm=JSON.parse(readFileSync(join(directory,'repayguard-realm.json'),'utf8'));const client=realm.clients[0];assert.equal(client.publicClient,true);assert.equal(client.attributes['pkce.code.challenge.method'],'S256');assert.equal(client.directAccessGrantsEnabled,false);assert.deepEqual(client.redirectUris,['https://app.example.com/']);assert.equal(realm.users,undefined);
  const nginx=readFileSync(join(directory,'nginx.conf'),'utf8');assert.match(nginx,/connect-src 'self' https:\/\/api.example.com https:\/\/login.example.com;/);assert.match(nginx,/runtime-config.json/);assert.match(nginx,/no-store/);
  assert.notEqual(spawnSync('python',args,{encoding:'utf8'}).status,0);
  for(const invalid of ['http://app.example.com','https://user:password@app.example.com','https://app.example.com/path','https://app.example.com?x=1',"https://bad'host.example.com"]){const bad=[...args];bad[2]=invalid;assert.notEqual(spawnSync('python',bad,{encoding:'utf8'}).status,0)}
 }finally{rmSync(directory,{recursive:true,force:true})}
});
