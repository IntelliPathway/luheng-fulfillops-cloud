import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const compose=readFileSync(new URL('../deploy/1panel/docker-compose.yml',import.meta.url),'utf8');
const nginx=readFileSync(new URL('../deploy/1panel/nginx.conf',import.meta.url),'utf8');
const envExample=readFileSync(new URL('../deploy/1panel/.env.example',import.meta.url),'utf8');
const webDockerfile=readFileSync(new URL('../Dockerfile.web',import.meta.url),'utf8');
const capacityCheck=readFileSync(new URL('../scripts/ecs-capacity-check.sh',import.meta.url),'utf8');

function serviceBlock(name){
  const match=compose.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-z][a-z0-9_-]*:\\n|^volumes:)`,'m'));
  assert.ok(match,`missing ${name} service`);
  return match[1];
}

test('keeps the ECS acceptance stack private and fail-closed',()=>{
  const postgres=serviceBlock('postgres');
  const api=serviceBlock('api');
  const worker=serviceBlock('worker');
  const web=serviceBlock('web');
  assert.doesNotMatch(postgres,/^    ports:/m);
  assert.doesNotMatch(api,/^    ports:/m);
  assert.match(web,/127\.0\.0\.1:\$\{APP_HTTP_PORT:-18080\}:80/);
  assert.match(api,/read_only: true/);
  assert.match(worker,/read_only: true/);
  assert.match(compose,/ENABLE_LIVE_MODEL_CALLS: \$\{ENABLE_LIVE_MODEL_CALLS:-false\}/);
  assert.match(compose,/FULFILLOPS_ENABLE_DSH_RUNTIME: \$\{FULFILLOPS_ENABLE_DSH_RUNTIME:-false\}/);
  assert.match(compose,/ENABLE_PAYMENT_SANDBOX: \$\{ENABLE_PAYMENT_SANDBOX:-false\}/);
  assert.match(compose,/JOB_BROKER_BACKEND: postgres-notify/);
  assert.match(nginx,/proxy_pass http:\/\/api:8000/);
  assert.match(nginx,/Content-Security-Policy/);
  assert.match(compose,/APP_ENV: \$\{APP_ENV:-production\}/);
  assert.match(compose,/AUTH_MODE: \$\{AUTH_MODE:-oidc\}/);
  assert.match(compose,/ALLOW_DEV_HEADER_AUTH: \$\{ALLOW_DEV_HEADER_AUTH:-false\}/);
  assert.match(compose,/ALLOW_DEV_TOKEN: \$\{ALLOW_DEV_TOKEN:-false\}/);
  assert.match(compose,/SEED_DEMO_DATA: \$\{SEED_DEMO_DATA:-false\}/);
  assert.match(compose,/AUTO_CREATE_SCHEMA: \$\{AUTO_CREATE_SCHEMA:-false\}/);
  assert.match(compose,/api\/v1\/health\/ready/);
  assert.match(envExample,/APP_ENV=production/);
  assert.match(envExample,/AUTH_MODE=oidc/);
  assert.match(envExample,/ALLOW_DEV_HEADER_AUTH=false/);
  assert.match(envExample,/ALLOW_DEV_TOKEN=false/);
  assert.match(envExample,/SEED_DEMO_DATA=false/);
  assert.match(envExample,/CHANGE_ME_/);
  assert.doesNotMatch(envExample,/DEEPSEEK_API_KEY=\S+/);
  assert.match(webDockerfile,/FROM node:22-alpine AS build/);
  assert.match(webDockerfile,/FROM nginx:1\.27-alpine/);
  assert.match(readFileSync(new URL('../backend/Dockerfile',import.meta.url),'utf8'),/USER 10001:10001/);
  assert.match(capacityCheck,/RESULT=FAIL/);
  assert.match(capacityCheck,/MEM_MIB < 3700/);
});
