import React,{useState} from 'react';
import {Tabs} from './ui';
import {CustomerMaterials} from './customer-materials';
import {CustomerSync} from './customer-sync';
import {MaterialReview} from './material-review';
import {CaseAcceptance} from './case-acceptance';
export function CustomerWorkflow(props){
 const [step,setStep]=useState('materials');const panels={materials:CustomerMaterials,sync:CustomerSync,review:MaterialReview,acceptance:CaseAcceptance};const Panel=panels[step];
 return <section className="customer-workflow" aria-label="客户接入与验收流程"><Tabs items={[{id:'materials',label:'接入材料'},{id:'sync',label:'同步事件'},{id:'review',label:'证据关联'},{id:'acceptance',label:'案例验收'}]} value={step} onChange={setStep}/><Panel key={step+props.tenant} {...props}/></section>
}
