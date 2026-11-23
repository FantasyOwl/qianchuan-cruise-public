/* Edit only gender, adult age bands and fan exclusion in our new task form. */
async function hybridAudienceDom(root,value,env){
  const {text,visible,exact,pause,clickNative,roots,chaseForm}=env;
  const labels={any:'不限',male:'男',female:'女'},ages=['18-23','24-30','31-40','41-49','50+'];
  if(!value||!Object.hasOwn(labels,value.gender)||!Array.isArray(value.ages)||value.ages.some(v=>!ages.includes(v))||new Set(value.ages).size!==value.ages.length||typeof value.exclude_fans!=='boolean')throw Error('本批定向规则无效');
  const buttons=[...new Set(exact(root,'设置定向').concat(exact(root,'+ 设置定向'),exact(root,'编辑定向'),exact(root,'修改定向')))];
  if(buttons.length!==1)throw Error('本批设置定向入口未唯一明确');
  clickNative(buttons[0]);
  let panel;
  for(let n=0;n<40;n++){
    const found=roots().filter(r=>!text(r).includes('任务名称')&&exact(r,'设置定向').length===1&&exact(r,'性别').length===1&&exact(r,'年龄').length===1&&exact(r,'抖音号粉丝').length===1);
    if(found.length===1){panel=found[0];break;}if(found.length>1)throw Error('设置定向窗口不唯一');await pause(100);
  }
  if(!panel)throw Error('设置定向窗口未就绪，未提交');
  function group(label,options){
    const heading=exact(panel,label);if(heading.length!==1)throw Error('定向分组未唯一明确：'+label);
    for(let box=heading[0].parentElement,n=0;box&&panel.contains(box)&&n<6;box=box.parentElement,n++){
      if(options.every(v=>exact(box,v).length===1)){
        if(['性别','年龄','抖音号粉丝'].filter(v=>v!==label).some(v=>exact(box,v).length))throw Error('定向控件跨分组：'+label);
        return box;
      }
    }
    throw Error('定向选项未加载：'+label);
  }
  function control(box,label){
    const items=exact(box,label).map(e=>e.closest('[role="radio"],[role="checkbox"],label,[class*="radio-item"],[class*="checkbox"]')||e);
    const unique=[...new Set(items)].filter(e=>box.contains(e)&&visible(e));if(unique.length!==1)throw Error('定向选项不唯一：'+label);return unique[0];
  }
  function selected(e){
    const input=e.matches('input')?e:e.querySelector('input[type="radio"],input[type="checkbox"]');
    if(input)return input.checked;
    const attr=e.getAttribute('aria-checked');if(attr==='true'||attr==='false')return attr==='true';
    return /(?:^|[-_\s])(?:checked|active|selected)(?:$|[-_\s])/.test(String(e.className));
  }
  async function choose(box,label){
    const e=control(box,label);if(!selected(e)){clickNative(e);await pause(100);}
    for(let n=0;!selected(e)&&n<25;n++)await pause(100);
    if(!selected(e))throw Error('定向选项未选中：'+label);
  }
  const gender=group('性别',['不限','男','女']),age=group('年龄',['不限',...ages]),fans=group('抖音号粉丝',['不限','排除']);
  await choose(gender,labels[value.gender]);await choose(age,'不限');
  // Vue commits the complete checkbox array on its next render. A second
  // click before that render can overwrite the preceding age selection.
  for(const band of ages){
    const e=control(age,band),want=value.ages.includes(band);
    if(selected(e)!==want){
      clickNative(e);await pause(100);
      for(let n=0;selected(control(age,band))!==want&&n<25;n++)await pause(100);
      if(selected(control(age,band))!==want)throw Error('定向年龄未同步：'+band);
    }
  }
  await choose(fans,value.exclude_fans?'排除':'不限');
  const validate=()=>Object.entries(labels).every(([key,label])=>selected(control(gender,label))===(key===value.gender))
    &&ages.every(a=>selected(control(age,a))===value.ages.includes(a))
    &&selected(control(age,'不限'))===(value.ages.length===0)
    &&selected(control(fans,'排除'))===value.exclude_fans&&selected(control(fans,'不限'))===!value.exclude_fans;
  for(let n=0;!validate()&&n<25;n++)await pause(100);
  if(!validate())throw Error('定向性别、年龄或粉丝排除未核对一致，未提交');
  const proof={gender:value.gender,ages:ages.filter(a=>value.ages.includes(a)),exclude_fans:value.exclude_fans,captured_at:new Date().toISOString(),source:'native_form'};
  const saves=[...panel.querySelectorAll('button,[role="button"]')].filter(e=>visible(e)&&['确定','完成','保存'].includes(text(e).replace(/\s/g,'')));
  if(saves.length===1)clickNative(saves[0]);
  else if(saves.length>1)throw Error('定向保存入口不唯一');
  else{
    const title=exact(panel,'设置定向');let back;
    if(title.length===1)for(let box=title[0].parentElement,n=0;box&&panel.contains(box)&&n<3;box=box.parentElement,n++){
      const icons=[...box.querySelectorAll('iconpark-icon[name*="left"],[data-icon*="arrow-left"],[aria-label="返回"]')].filter(visible);
      if(icons.length===1){back=icons[0];break;}
    }
    if(!back)throw Error('定向返回入口未明确，未提交');clickNative(back);
  }
  root=await chaseForm();
  function summary(){
    const heading=exact(root,'定向人群');if(heading.length!==1)return null;
    for(let box=heading[0].parentElement,n=0;box&&root.contains(box)&&n<5;box=box.parentElement,n++){
      const s=text(box);
      if(s.includes('任务名称')||s.includes('调控日预算'))break;
      if((value.gender==='any'||s.includes(labels[value.gender]))&&value.ages.every(a=>s.includes(a))&&(!value.exclude_fans||/排除/.test(s)&&/粉丝/.test(s)))return box;
    }
    return null;
  }
  const section=summary();if(!section)throw Error('定向保存后摘要未显示所选条件，未提交');
  const signature=text(section);
  return {root,proof,check:()=>visible(root)&&section.isConnected&&text(section)===signature};
}
if(typeof module==='object'&&module.exports)module.exports={hybridAudienceDom};
