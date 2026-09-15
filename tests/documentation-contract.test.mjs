import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

const projectRoot=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const stableDocs=[
  'README.md',
  'docs/README.md',
  'docs/brand-guide.md',
  'docs/product-functional-spec.md',
  'docs/architecture.md',
  'docs/configuration.md',
  'docs/operations-runbook.md',
  'deploy/1panel/README.md',
  'SECURITY.md',
  'CONTRIBUTING.md',
];

test('keeps every stable documentation entry present',()=>{
  for(const relativePath of stableDocs){
    assert.ok(existsSync(resolve(projectRoot,relativePath)),'missing stable document: '+relativePath);
  }
});

test('keeps local links in stable documentation resolvable',()=>{
  const broken=[];
  for(const relativePath of stableDocs){
    const sourcePath=resolve(projectRoot,relativePath);
    const source=readFileSync(sourcePath,'utf8');
    for(const match of source.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)){
      const target=match[1].trim().split('#')[0];
      if(!target||/^(?:https?:|mailto:|#)/.test(target))continue;
      const decoded=decodeURIComponent(target);
      if(!existsSync(resolve(dirname(sourcePath),decoded)))broken.push(relativePath+' -> '+target);
    }
  }
  assert.deepEqual(broken,[]);
});

test('keeps startup, product, architecture and operations documents purpose-specific',()=>{
  const read=relativePath=>readFileSync(resolve(projectRoot,relativePath),'utf8');
  assert.match(read('README.md'),/## 三步开机/);
  assert.match(read('README.md'),/docker compose up --build -d/);
  assert.match(read('docs/product-functional-spec.md'),/## 3\. 角色与职责/);
  assert.match(read('docs/product-functional-spec.md'),/## 8\. 发布验收/);
  assert.match(read('docs/architecture.md'),/## 2\. 运行拓扑/);
  assert.match(read('docs/operations-runbook.md'),/备份/);
});
