import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source=await readFile(new URL('../src/oidc-client.js',import.meta.url),'utf8');

test('uses authorization code with PKCE and state validation',()=>{
  assert.match(source,/response_type:'code'/);
  assert.match(source,/code_challenge_method:'S256'/);
  assert.match(source,/flow\.state!==state/);
  assert.doesNotMatch(source,/response_type:'token'/);
});

test('refreshes tokens and clears invalid sessions',()=>{
  assert.match(source,/grant_type:'refresh_token'/);
  assert.match(source,/clearOidcSession\(\)/);
  assert.match(source,/expires_at/);
});
