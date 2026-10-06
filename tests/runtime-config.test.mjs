import assert from 'node:assert/strict';
import test from 'node:test';
import worker,{publicConfig} from '../worker/index.js';
import {initializePublicConfig,publicRuntimeConfig,validateRuntimeConfig} from '../src/runtime-config.js';
import {classifyApiFailure} from '../src/api.js';

const env={PUBLIC_API_BASE_URL:'https://api.example.com/api/v1',PUBLIC_OIDC_AUTHORITY:'https://idp.example.com',
 PUBLIC_OIDC_AUTHORIZATION_ENDPOINT:'https://idp.example.com/authorize',PUBLIC_OIDC_TOKEN_ENDPOINT:'https://idp.example.com/token',
 PUBLIC_OIDC_LOGOUT_ENDPOINT:'https://idp.example.com/logout',PUBLIC_OIDC_CLIENT_ID:'browser-public-client',PUBLIC_OIDC_AUDIENCE:'repayguard',
 PRIVATE_KEY:'not-public',ASSETS:{fetch:async()=>new Response('app')}};

test('runtime config is public-only and expands CSP only to configured origins',async()=>{
 const r=await worker.fetch(new Request('https://site.example.com/runtime-config.json'),env);
 assert.equal(r.status,200);const config=await r.json();
 assert.equal(config.api_base_url,env.PUBLIC_API_BASE_URL);assert.equal(config.mode,'connected');
 assert.ok(!JSON.stringify(config).includes('not-public'));
 assert.match(r.headers.get('Content-Security-Policy'),/connect-src 'self' https:\/\/api.example.com https:\/\/idp.example.com/);
 assert.equal(r.headers.get('Cache-Control'),'no-store');
 assert.deepEqual(publicConfig(),{schema_version:1,mode:'demo'});
 for(const bad of ['http://api.example.com','https://user:secret@api.example.com','https://127.0.0.1','https://api.internal','https://api.example.com?token=bad']){
  const failed=await worker.fetch(new Request('https://site.example.com/runtime-config.json'),{...env,PUBLIC_API_BASE_URL:bad});
  assert.equal(failed.status,503);
 }
 assert.throws(()=>publicConfig({...env,PUBLIC_OIDC_CLIENT_ID:''}));
 assert.throws(()=>validateRuntimeConfig({schema_version:1,mode:'connected'}));
});

test('configured backend network failure never becomes an offline write',async()=>{
 const old=globalThis.fetch;
 try{globalThis.fetch=async()=>new Response(JSON.stringify(publicConfig(env)),{status:200});await initializePublicConfig();
 assert.equal(publicRuntimeConfig().mode,'connected');
 assert.equal(classifyApiFailure(new TypeError('network failure')),'api-error');
 globalThis.fetch=async()=>new Response(JSON.stringify({schema_version:1,mode:'demo'}));await initializePublicConfig();
 }finally{globalThis.fetch=old}
});
