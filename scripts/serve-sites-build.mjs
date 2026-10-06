// QA-only adapter for the exact packaged Worker and client assets. No business API proxy.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
import worker from '../dist/server/index.js';
const root=resolve('dist/client');
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.json':'application/json'};
const env={ASSETS:{fetch:async request=>{const pathname=decodeURIComponent(new URL(request.url).pathname);const file=resolve(root,'.'+pathname);if(!file.startsWith(root+sep))return new Response('Not found',{status:404});try{return new Response(await readFile(file),{headers:{'Content-Type':types[extname(file)]||'application/octet-stream'}})}catch{return new Response('Not found',{status:404})}}}};
createServer(async(req,res)=>{try{const chunks=[];for await(const chunk of req)chunks.push(chunk);const request=new Request(`http://127.0.0.1:5180${req.url}`,{method:req.method,headers:req.headers,...(['GET','HEAD'].includes(req.method)?{}:{body:Buffer.concat(chunks)})});const response=await worker.fetch(request,env);res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()))}catch{res.writeHead(500);res.end('QA adapter failed')}}).listen(5180,'127.0.0.1',()=>console.log('Packaged Sites QA ready'));
