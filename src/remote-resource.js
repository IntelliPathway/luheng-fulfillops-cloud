import {useEffect,useRef,useState} from 'react';

// Clear evidence on every scope/reload and ignore late responses after navigation.
export function useRemoteResource(enabled,scope,load){
 const generation=useRef(0),loader=useRef(load);loader.current=load;
 const [revision,setRevision]=useState(0),[state,setState]=useState({scope:'',data:null,busy:false,error:''});
 useEffect(()=>{
  const id=++generation.current;setState({scope,data:null,busy:enabled,error:''});
  if(enabled)Promise.resolve().then(()=>loader.current()).then(data=>{if(id===generation.current)setState({scope,data,busy:false,error:''})}).catch(error=>{if(id===generation.current)setState({scope,data:null,busy:false,error:error.message||'服务暂时不可用'})});
  return()=>{generation.current++};
 },[enabled,scope,revision]);
 return {...(state.scope===scope?state:{data:null,busy:enabled,error:''}),refresh:()=>setRevision(n=>n+1)};
}
