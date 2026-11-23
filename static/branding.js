'use strict';
(function(global){
 const DEFAULT={name:'千川巡航',subtitle:'多账户调控台',icon_data_url:null};
 const copy=value=>({...value});
 class Panel{
  constructor(app){this.app=app;this.doc=app.doc;this.dialog=app.$('#branding-dialog');this.saved=copy(DEFAULT);this.draft=copy(DEFAULT);this.busy=false;this.imageSequence=0;if(this.dialog)this.bind();}
  $(selector){return this.doc.querySelector(selector);}
  apply(value){
   this.saved={...DEFAULT,...value};
   this.$('#brand-name').textContent=this.saved.name;this.$('#brand-subtitle').textContent=this.saved.subtitle;
   this.$('.brand').title=this.saved.name+(this.saved.subtitle?' · '+this.saved.subtitle:'');
   this.mark('brand',this.saved);this.doc.title=this.saved.name+' · 多账户调控';
  }
  mark(prefix,value){
   const img=this.$('#'+prefix+'-icon'),text=this.$('#'+prefix+'-fallback');
   const allowed=typeof value.icon_data_url==='string'&&/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(value.icon_data_url);
   img.onerror=()=>{img.hidden=true;text.hidden=false;};
   img.hidden=!allowed;text.hidden=allowed;
   if(allowed)img.src=value.icon_data_url;else img.removeAttribute('src');
   text.textContent=value.name===DEFAULT.name?'巡':Array.from(value.name||'巡')[0];
  }
  async load(){try{const response=await this.app.api('/api/branding');this.apply(response.branding);}catch{this.apply(this.saved);}}
  open(){
   this.imageSequence++;this.draft=copy(this.saved);this.$('#branding-name').value=this.draft.name;this.$('#branding-subtitle').value=this.draft.subtitle;
   this.$('#branding-file').value='';this.feedback('');this.preview();this.dialog.showModal();
  }
  preview(){
   this.$('#branding-preview-name').textContent=this.draft.name||'填写名称';
   this.$('#branding-preview-subtitle').textContent=this.draft.subtitle;
   this.mark('branding-preview',this.draft);
   this.$('#branding-remove-icon').disabled=!this.draft.icon_data_url||this.busy;
  }
  feedback(message,error=false){const el=this.$('#branding-feedback');el.textContent=message;el.classList.toggle('error',error);}
  async image(file){
   if(!file)return;
   const sequence=++this.imageSequence;
   this.uploading=true;this.$('#branding-save').disabled=true;this.feedback('正在压缩并预览图标…');
   try{
    if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('请选择PNG、JPG或WebP图片');
    if(file.size>2*1024*1024)throw Error('请选择2MB以内的图标');
    const data=await new Promise((resolve,reject)=>{const r=new this.doc.defaultView.FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('无法读取这张图片'));r.readAsDataURL(file);});
    const img=await new Promise((resolve,reject)=>{const i=new this.doc.defaultView.Image();i.onload=()=>resolve(i);i.onerror=()=>reject(Error('无法打开这张图片，请换一张'));i.src=data;});
    if(!img.naturalWidth||!img.naturalHeight||img.naturalWidth>4096||img.naturalHeight>4096)throw Error('请选择宽、高不超过4096像素的图标');
    const canvas=this.doc.createElement('canvas');canvas.width=canvas.height=128;
    const ctx=canvas.getContext('2d'),scale=Math.min(128/img.naturalWidth,128/img.naturalHeight),w=img.naturalWidth*scale,h=img.naturalHeight*scale;
    ctx.drawImage(img,(128-w)/2,(128-h)/2,w,h);
    if(sequence!==this.imageSequence||!this.dialog.open)return;
    this.draft.icon_data_url=canvas.toDataURL('image/png');this.preview();this.feedback('图标已预览，点击保存后生效。');
   }catch(error){if(sequence===this.imageSequence)this.feedback(error.message,true);}
   finally{if(sequence===this.imageSequence){this.uploading=false;this.$('#branding-save').disabled=this.busy;}}
  }
  async save(){
   if(this.busy||this.uploading)return;
   const name=this.$('#branding-name').value.trim(),subtitle=this.$('#branding-subtitle').value.trim();
   if(!name||Array.from(name).length>32||Array.from(subtitle).length>48){this.feedback('名称须为1–32个字，副标题最多48个字。',true);return;}
   const value={...this.draft,name,subtitle};this.busy=true;
   for(const el of this.dialog.querySelectorAll('input,button'))el.disabled=true;
   this.feedback('正在保存…');
   try{const response=await this.app.api('/api/branding',value,{signal:AbortSignal.timeout(10000)});this.apply(response.branding);this.dialog.close();this.app.note('名称和图标已保存。');}
   catch(error){this.feedback(error.message,true);}
   finally{this.busy=false;for(const el of this.dialog.querySelectorAll('input,button'))el.disabled=false;this.preview();}
  }
  bind(){
   this.$('#branding-edit').addEventListener('click',()=>this.open());
   this.dialog.addEventListener('close',()=>{this.imageSequence++;this.uploading=false;this.$('#branding-save').disabled=false;});
   this.dialog.addEventListener('cancel',event=>{if(this.busy)event.preventDefault();});
   this.$('#branding-form').addEventListener('submit',event=>{event.preventDefault();void this.save();});
   this.$('#branding-cancel').addEventListener('click',()=>this.dialog.close());
   for(const key of ['name','subtitle'])this.$('#branding-'+key).addEventListener('input',event=>{this.draft[key]=event.target.value;this.preview();});
   this.$('#branding-file').addEventListener('change',event=>void this.image(event.target.files[0]));
   this.$('#branding-remove-icon').addEventListener('click',()=>{this.imageSequence++;this.uploading=false;this.draft.icon_data_url=null;this.$('#branding-file').value='';this.$('#branding-save').disabled=false;this.preview();this.feedback('图标已移除，点击保存后生效。');});
   this.$('#branding-reset').addEventListener('click',()=>{this.imageSequence++;this.uploading=false;this.draft=copy(DEFAULT);this.$('#branding-name').value=DEFAULT.name;this.$('#branding-subtitle').value=DEFAULT.subtitle;this.$('#branding-file').value='';this.$('#branding-save').disabled=false;this.preview();this.feedback('已恢复默认预览，点击保存后生效。');});
  }
 }
 global.QianchuanBranding={Panel};if(typeof module==='object'&&module.exports)module.exports={Panel,DEFAULT};
})(typeof window==='object'?window:globalThis);
