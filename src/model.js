import rawCases from './data/cases.json';
import rawPackages from './data/packages.json';
import rawPlans from './data/plans.json';
import ledger from './data/expected_ledger.json';
import schedules from './data/installment_schedules.json';

export const money = n => (Number(n)<0?'-¥':'¥') + Math.abs(Number(n || 0)).toLocaleString('zh-CN', { minimumFractionDigits: Number.isInteger(Number(n || 0)) ? 0 : 2, maximumFractionDigits: 2 });
export const titles = {overview:'工作台',control:'AI 指挥台',pilot:'试点验收',activities:'履约活动',assets:'资产包',cases:'案件',plans:'履约计划',payments:'回款与佣金',agents:'Agents',exceptions:'异常中心',logs:'运行记录',strategy:'策略与知识',integrations:'AI 与渠道',usage:'用量与账单',settings:'组织设置'};
export const packages = rawPackages.map((p,i)=>({...p,title:['长龄个贷一期','长龄个贷二期','消费个贷三期'][i],rate:[.15,.18,.2][i]}));
export const plans = rawPlans;
export const baseLedger = ledger;
export const installmentSchedules = schedules;
const statuses=['已结清','履约中','部分履约','退款已核销','自然回款','到账待匹配','支付失败','待联系','待联系','异议暂停','资料待补','金额待核','停止联系','委托到期','委托到期','待联系','支付处理中','授权待补','预算耗尽','身份待核','已确认回款','号码缺失','方案待签','待联系'];
const reasons={C010:'存在未解决异议，触达已暂停',C011:'委托资料不完整，补齐后重新校验',C012:'金额核验未通过，等待复核',C013:'已记录停止联系请求',C014:'委托已于 2026.08.31 到期，仅处理尾期回款',C015:'委托已于 2026.07.31 到期',C018:'缺少有效授权策略',C019:'单案预算已耗尽',C020:'身份核验存在不一致',C022:'缺少有效联系号码'};
const contactDates=['2026.09.08','2026.09.10','2026.09.05','2026.09.07','2026.09.04','2026.09.03','2026.09.01','2026.08.29','2026.09.02','2026.09.11','—','2026.09.06','2026.09.09','2026.08.28','2026.07.30','2026.08.30','2026.09.10','—','2026.09.01','2026.09.08','2026.09.10','—','2026.09.08','2026.09.07'];
const nextDates=['归档','2026.09.13 10:00','2026.09.13 11:30','2026.09.14 09:30','2026.09.14 14:00','2026.09.15 10:00','2026.09.15 15:00','2026.09.16 09:30','2026.09.13 09:30','异议解决后','资料补齐后','金额核验后','已停止联系','仅核对到账','委托已到期','2026.09.16 14:00','等待支付结果','授权补齐后','预算调整后','身份复核后','持续核对到账','号码补齐后','方案签署后','2026.09.14 10:30'];
export function caseModels(rows, paid){return rawCases.map((c,i)=>{
 const tx=rows.filter(t=>t.case_id===c.case_id&&t.tenant_id===c.tenant_id);
 const ageMonths=Math.max(1,Math.round((new Date('2026-09-12')-new Date(c.first_overdue_date))/(1000*60*60*24*30.44)));
 const quality=[96,94,92,90,89,88,91,87,86,95,63,72,93,91,90,85,88,58,84,77,94,42,89,90][i]||85;
 return {...c,status:c.case_id==='C002'&&paid?'本期已足额':statuses[i],reason:reasons[c.case_id]||'',blocked:!!reasons[c.case_id],cash:tx.reduce((s,t)=>s+t.cash_yuan,0),commission:tx.reduce((s,t)=>s+t.commission_yuan,0),plan:plans.find(p=>p.case_id===c.case_id&&p.tenant_id===c.tenant_id),ageMonths,agingBucket:ageMonths>=60?'5年以上':ageMonths>=36?'3–5年':'1–3年',dataQuality:quality,lastContact:contactDates[i]||'—',nextAllowed:nextDates[i]||'待计划',contactability:c.phone_token?.includes('NON_DIALABLE')?'模拟号码':'可触达'};
});}
export const initialActivities=[
 {id:'ACT-001',tenant:'TENANT_A',name:'履约补款跟进',package:'PKG_A',caseIds:['C002'],status:'running',goal:'已签协议履约',next:'补款差额 ¥1,016',updated:'10:42',version:1,budget:30},
 {id:'ACT-002',tenant:'TENANT_A',name:'一次性方案履约',package:'PKG_A',caseIds:['C003'],status:'running',goal:'已签协议履约',next:'核对剩余 ¥5,000',updated:'10:36',version:1,budget:30},
 {id:'ACT-003',tenant:'TENANT_A',name:'已结清归档',package:'PKG_A',caseIds:['C001'],status:'completed',goal:'履约归档',next:'回款 ¥10,000 已确认',updated:'09:58',version:1,budget:30},
 {id:'ACT-004',tenant:'TENANT_A',name:'异议案件保护',package:'PKG_B',caseIds:['C010'],status:'blocked',goal:'异议保护',next:'等待异议处理结果',updated:'09:45',version:1,budget:30},
 {id:'ACT-005',tenant:'TENANT_A',name:'委托到期管理',package:'PKG_B',caseIds:['C014'],status:'blocked',goal:'尾期回款核对',next:'仅核对尾期回款',updated:'09:30',version:1,budget:30},
 {id:'ACT-006',tenant:'TENANT_B',name:'确认回款归集',package:'PKG_C',caseIds:['C021'],status:'running',goal:'回款自动核对',next:'持续核对回款记录',updated:'10:18',version:1,budget:30}
];
export const reasonText={IN_MANDATE:'委托期内回款',REFUND_ORIGINAL_RATE:'按原比例冲回',SIGNED_PLAN_TAIL:'已签方案尾期回款',OUTSIDE_TAIL:'超出计佣尾期',PRE_MANDATE:'委托前回款'};
export const initialEvents=[
 {id:'EV-0',tenant:'TENANT_A',time:'10:44:03',caseId:'C004',title:'语音回执结果待确认',detail:'TTS 网关首次请求超时；已停止本次外呼并在 48 小时冷却后重试',type:'exception',runId:'RUN-A006-C004',stepId:'STEP-04',actionId:'ACTN-1031',actor:'Hermes Agent',tool:'voice.synthesize',status:'等待重试',version:'VOICE v2.1',latency:'2400 ms',cost:'¥0.00'},
 {id:'EV-1',tenant:'TENANT_A',time:'10:42:18',caseId:'C002',title:'差额已识别',detail:'本期剩余应还 ¥1,016',type:'decision',runId:'RUN-A001-C002',stepId:'STEP-03',actionId:'ACTN-1028',actor:'规则引擎',tool:'payment.calculate_gap',status:'已完成',version:'POL_A v1.0',latency:'42 ms',cost:'¥0.00'},
 {id:'EV-2',tenant:'TENANT_A',time:'10:36:04',caseId:'C003',title:'到账核验完成',detail:'已确认到账 ¥3,000',type:'payment',runId:'RUN-A002-C003',stepId:'STEP-02',actionId:'ACTN-1022',actor:'账务接口',tool:'payment.reconcile',status:'已完成',version:'COM_A v1.0',latency:'318 ms',cost:'¥0.00'},
 {id:'EV-3',tenant:'TENANT_A',time:'09:58:32',caseId:'C001',title:'履约完成',detail:'应计佣金 ¥1,500',type:'completed',runId:'RUN-A003-C001',stepId:'STEP-06',actionId:'ACTN-0998',actor:'工作流',tool:'case.close',status:'已完成',version:'FLOW v3.2',latency:'71 ms',cost:'¥0.00'},
 {id:'EV-4',tenant:'TENANT_A',time:'09:45:11',caseId:'C010',title:'异议保护已生效',detail:'触达已自动暂停',type:'exception',runId:'RUN-A004-C010',stepId:'STEP-01',actionId:'ACTN-0987',actor:'保护规则',tool:'contact.hold',status:'已阻断',version:'GUARD v2.1',latency:'19 ms',cost:'¥0.00'},
 {id:'EV-5',tenant:'TENANT_B',time:'10:18:26',caseId:'C021',title:'两笔回款已归集',detail:'累计确认到账 ¥800',type:'payment',runId:'RUN-B001-C021',stepId:'STEP-02',actionId:'ACTN-1012',actor:'账务接口',tool:'payment.reconcile',status:'已完成',version:'COM_C v1.0',latency:'284 ms',cost:'¥0.00'}
];

export const exceptionMeta={
 C010:{id:'EX-20260912-010',category:'债务异议',priority:'P0',owner:'AMC 异议专员',sla:'剩余 1小时 18分',created:'2026.09.12 09:45',condition:'异议结论及金额依据回执完成后，可重新评估触达。'},
 C011:{id:'EX-20260911-011',category:'资料缺失',priority:'P1',owner:'委案资料管理员',sla:'剩余 18小时',created:'2026.09.11 16:20',condition:'补齐合同、债权链和联系授权后重新预检。'},
 C012:{id:'EX-20260912-012',category:'金额冲突',priority:'P0',owner:'账务复核员',sla:'剩余 2小时 40分',created:'2026.09.12 08:54',condition:'余额快照与账务流水复核一致后解除。'},
 C013:{id:'EX-20260910-013',category:'停止联系',priority:'P0',owner:'消保负责人',sla:'持续保护',created:'2026.09.10 14:16',condition:'不允许自动解除；仅处理合法有效的后续请求。'},
 C014:{id:'EX-20260901-014',category:'委托到期',priority:'P1',owner:'合同管理员',sla:'尾期跟踪',created:'2026.09.01 00:00',condition:'禁止新触达，仅允许核对已签方案尾期到账。'},
 C015:{id:'EX-20260801-015',category:'委托到期',priority:'P1',owner:'合同管理员',sla:'已超期',created:'2026.08.01 00:00',condition:'保持停止触达，回款按尾期规则判断计佣。'},
 C018:{id:'EX-20260912-018',category:'授权缺失',priority:'P0',owner:'策略管理员',sla:'剩余 42分钟',created:'2026.09.12 10:06',condition:'有效策略审批发布后重新预检。'},
 C019:{id:'EX-20260912-019',category:'预算耗尽',priority:'P2',owner:'运营负责人',sla:'剩余 6小时',created:'2026.09.12 07:20',condition:'调整预算或关闭任务。'},
 C020:{id:'EX-20260912-020',category:'身份冲突',priority:'P0',owner:'身份复核员',sla:'剩余 3小时',created:'2026.09.12 08:11',condition:'身份资料核验一致前禁止披露债务信息。'},
 C022:{id:'EX-20260911-022',category:'号码缺失',priority:'P2',owner:'数据管理员',sla:'剩余 20小时',created:'2026.09.11 15:02',condition:'补充具备使用依据的有效联系方式。'}
};
export function downloadCSV(name,head,rows){const escape=v=>'"'+String(v??'').replaceAll('"','""')+'"';const blob=new Blob(['\ufeff'+[head,...rows].map(r=>r.map(escape).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8;'});const u=URL.createObjectURL(blob);const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
