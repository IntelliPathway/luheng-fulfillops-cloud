let runtime={};
export const publicRuntimeConfig=()=>runtime;

export function validateRuntimeConfig(value){
 if(!value||value.schema_version!==1||!['demo','connected'].includes(value.mode))throw new Error('站点接入配置格式无效');
 if(value.mode==='demo')return {mode:'demo'};
 for(const key of ['api_base_url','oidc_authority','oidc_authorization_endpoint','oidc_token_endpoint','oidc_logout_endpoint']){
  const url=new URL(value[key]);
  if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw new Error('接入地址必须是无凭据的 HTTPS URL');
 }
 if(!value.oidc_client_id||!value.oidc_audience)throw new Error('业务接入需要完整企业登录配置');
 return value;
}

export async function initializePublicConfig(){
 const response=await fetch('/runtime-config.json',{cache:'no-store',redirect:'error'});
 if(response.status===404)return; // Local Vite/standalone deployment uses build-time config.
 if(!response.ok)throw new Error('站点接入配置不可用，请联系管理员');
 runtime=validateRuntimeConfig(await response.json());
}
