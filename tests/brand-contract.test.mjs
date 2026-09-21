import assert from 'node:assert/strict';
import {readFileSync,statSync} from 'node:fs';
import test from 'node:test';

import {
  APP_VERSION,
  BRAND_ASSET,
  PRODUCT_DESCRIPTOR,
  PRODUCT_ENGLISH_NAME,
  PRODUCT_NAME,
} from '../src/brand.js';

const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');

test('keeps the customer-facing brand centralized and complete',()=>{
  assert.equal(PRODUCT_NAME,'履约智控 AI');
  assert.equal(PRODUCT_ENGLISH_NAME,'RepayGuard AI');
  assert.equal(PRODUCT_DESCRIPTOR,'资产回款运营平台');
  assert.equal(APP_VERSION,'1.9.0');
  assert.equal(BRAND_ASSET,'/assets/repayguard-ai-mark.png');

  const asset=new URL('../public'+BRAND_ASSET,import.meta.url);
  assert.ok(statSync(asset).size>1000);
  assert.deepEqual([...readFileSync(asset).subarray(0,8)],[137,80,78,71,13,10,26,10]);
});

test('keeps current UI and stable documentation on the new brand',()=>{
  const currentFiles=[
    '../index.html',
    '../src/App.jsx',
    '../src/ai.jsx',
    '../README.md',
    '../backend/README.md',
    '../docs/README.md',
    '../docs/brand-guide.md',
    '../docs/product-functional-spec.md',
    '../docs/architecture.md',
  ].map(read);
  const content=currentFiles.join('\n');

  assert.match(content,/履约智控 AI/);
  assert.match(content,/RepayGuard AI/);
  assert.match(content,/repayguard-ai-mark\.png/);
  assert.doesNotMatch(content,/履衡 AI/);
  assert.doesNotMatch(content,/FulfillOps Cloud/);
  assert.doesNotMatch(content,/FULFILLOPS STARTUP GATE/);
});

test('keeps release versions aligned across application and deployment surfaces',()=>{
  const escapedVersion=APP_VERSION.replaceAll('.','\\.');
  assert.equal(JSON.parse(read('../package.json')).version,APP_VERSION);
  assert.match(read('../backend/app/version.py'),new RegExp('APP_VERSION = "'+escapedVersion+'"'));
  assert.match(read('../backend/pyproject.toml'),new RegExp('version = "'+escapedVersion+'"'));
  assert.match(read('../deploy/1panel/docker-compose.yml'),new RegExp('luheng-fulfillops-api:'+escapedVersion));
  assert.match(read('../deploy/1panel/docker-compose.yml'),new RegExp('luheng-fulfillops-web:'+escapedVersion));
});
