/* Compatibility for sandboxed previews; never changes server authentication. */
(() => {
 function storage(type){
  const memory=new Map();let backing;
  try{backing=window[type];const key='coinw-storage-probe-'+Date.now()+'-'+Math.random();backing.setItem(key,'1');backing.removeItem(key);}catch{backing=null;}
  return {
   get persistent(){return !!backing;},
   getItem(key){if(backing){try{const value=backing.getItem(key);if(value===null)memory.delete(key);else memory.set(key,value);return value;}catch{backing=null;}}return memory.get(key)??null;},
   setItem(key,value){memory.set(key,String(value));if(backing){try{backing.setItem(key,String(value));}catch{backing=null;}}},
   removeItem(key){memory.delete(key);if(backing){try{backing.removeItem(key);}catch{backing=null;}}}
  };
 }
 window.previewStorage={local:storage('localStorage'),session:storage('sessionStorage')};
 const randomBytes=length=>{if(!globalThis.crypto?.getRandomValues)throw new Error('当前预览不支持安全随机数，请下载 HTML 后用系统浏览器打开。');return crypto.getRandomValues(new Uint8Array(length));};
 window.previewCrypto={
  randomBytes,
  uuid(){const bytes=randomBytes(16);bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const value=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');return value.slice(0,8)+'-'+value.slice(8,12)+'-'+value.slice(12,16)+'-'+value.slice(16,20)+'-'+value.slice(20);},
  async derive(password,salt){
   const encoded=new TextEncoder().encode(password);
   if(globalThis.crypto?.subtle){try{const material=await crypto.subtle.importKey('raw',encoded,'PBKDF2',false,['deriveBits']);return new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',salt,iterations:120000,hash:'SHA-256'},material,256));}catch(error){if(!['NotSupportedError','SecurityError'].includes(error.name))throw error;}}
   return window.coinwPbkdf2(encoded,salt,120000);
  }
 };
})();
