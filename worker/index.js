const securityHeaders = {
  "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "SAMEORIGIN",
};

export function publicConfig(env={}) {
  if(!env.PUBLIC_API_BASE_URL)return {schema_version:1,mode:'demo'};
  const values={api_base_url:env.PUBLIC_API_BASE_URL,oidc_authority:env.PUBLIC_OIDC_AUTHORITY,
    oidc_authorization_endpoint:env.PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,oidc_token_endpoint:env.PUBLIC_OIDC_TOKEN_ENDPOINT,
    oidc_logout_endpoint:env.PUBLIC_OIDC_LOGOUT_ENDPOINT};
  for(const [key,value] of Object.entries(values)){
    const url=new URL(value);
    if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||
       !url.hostname.includes('.')||/^[\d.]+$/.test(url.hostname)||url.hostname.includes(':')||
       /(^|\.)(localhost|local|internal|test)$/.test(url.hostname))throw new Error('Invalid public endpoint');
    values[key]=url.href.replace(/\/$/,'');
  }
  if(!env.PUBLIC_OIDC_CLIENT_ID||!env.PUBLIC_OIDC_AUDIENCE)throw new Error('Incomplete OIDC config');
  return {schema_version:1,mode:'connected',...values,oidc_client_id:env.PUBLIC_OIDC_CLIENT_ID,oidc_audience:env.PUBLIC_OIDC_AUDIENCE};
}

function secure(response, request, config={mode:'demo'}) {
  const secured = new Response(response.body, response);
  for (const [name, value] of Object.entries(securityHeaders)) secured.headers.set(name, value);
  if(config.mode==='connected'){
    const origins=[...new Set(['api_base_url','oidc_authority','oidc_authorization_endpoint','oidc_token_endpoint','oidc_logout_endpoint'].map(key=>new URL(config[key]).origin))];
    secured.headers.set('Content-Security-Policy',securityHeaders['Content-Security-Policy'].replace("connect-src 'self'",`connect-src 'self' ${origins.join(' ')}`));
  }
  if (new URL(request.url).protocol === "https:") {
    secured.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  }
  return secured;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let config;
    try{config=publicConfig(env)}catch{
      return secure(new Response(JSON.stringify({detail:'站点接入配置异常'}),{status:503,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}}),request);
    }
    if(url.pathname==='/runtime-config.json'){
      return secure(new Response(request.method==='HEAD'?null:JSON.stringify(config),{
        status:['GET','HEAD'].includes(request.method)?200:405,
        headers:{'Content-Type':'application/json','Cache-Control':'no-store'},
      }),request,config);
    }
    if (url.pathname.startsWith("/api/")) {
      return secure(new Response(JSON.stringify({
        detail: "Sites 在线交互演示未连接履约智控业务 API",
        mode: "sites-demo",
      }), {
        status: 503,
        headers: {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
      }), request, config);
    }

    const response = await env.ASSETS.fetch(request);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");

    if (response.status !== 404 || !acceptsHtml || !["GET", "HEAD"].includes(request.method)) {
      return secure(response, request, config);
    }

    const indexUrl = new URL(url);
    indexUrl.pathname = "/index.html";
    indexUrl.search = "";
    return secure(await env.ASSETS.fetch(new Request(indexUrl, request)), request, config);
  },
};
