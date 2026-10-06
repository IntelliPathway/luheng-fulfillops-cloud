import {defineConfig} from '@playwright/test';

export default defineConfig({
 testDir:'./tests/browser',workers:1,timeout:30000,
 use:{baseURL:'http://127.0.0.1:5178',viewport:{width:1440,height:1000}},
 webServer:[
  {command:'python -m uvicorn app.main:app --host 127.0.0.1 --port 8000',cwd:'backend',url:'http://127.0.0.1:8000/api/v1/health',reuseExistingServer:false,
   env:{DATABASE_URL:`sqlite:////tmp/repayguard-e2e-${process.pid}.db`,APP_ENV:'development',AUTH_MODE:'development',ALLOW_DEV_HEADER_AUTH:'true',ALLOW_DEV_TOKEN:'true',SEED_DEMO_DATA:'true',AUTO_CREATE_SCHEMA:'true',JOB_EXECUTION_MODE:'inline',SECRET_MASTER_KEY:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',SECRET_MASTER_KEY_VERSION:'browser-synthetic-v1'}},
  {command:'npm run dev -- --host 127.0.0.1 --port 5178',url:'http://127.0.0.1:5178',reuseExistingServer:false},
 ],
});
