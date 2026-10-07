import React,{useState,useEffect} from 'react';
import {Tabs} from './ui';
import {CustomerMaterials} from './customer-materials';
import {CustomerConnectors} from './customer-connectors';
import {CustomerSync} from './customer-sync';
import {MaterialReview} from './material-review';
import {EvidenceWorkspace} from './evidence-workspace';
import {CaseAcceptance} from './case-acceptance';
export function CustomerWorkflow(props){
 const [step,setStep]=useState('materials');const panels={evidence:EvidenceWorkspace,connectors:CustomerConnectors,materials:CustomerMaterials,sync:CustomerSync,review:MaterialReview,acceptance:CaseAcceptance};const Panel=panels[step];useEffect(()=>{if(props.requestedStep?.id&&panels[props.requestedStep.id])setStep(props.requestedStep.id)},[props.requestedStep]);
 return <section className="customer-workflow" aria-label="客户接入与验收流程"><Tabs items={[{id:'connectors',label:'业务连接器'},{id:'materials',label:'接入材料'},{id:'sync',label:'同步事件'},{id:'review',label:'证据关联'},{id:'evidence',label:'证据工作台'},{id:'acceptance',label:'案例验收'}]} value={step} onChange={setStep}/><Panel key={step+props.tenant} {...props}/></section>
}
