/* Minimal OOXML workbook with stored ZIP entries; no CDN or third-party runtime. */
window.makeExcelWorkbook=(sheets)=>{
 const encoder=new TextEncoder();
 const xml=s=>String(s??'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
 const col=n=>{let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
 const files=[];
 const add=(name,content)=>files.push({name:encoder.encode(name),data:encoder.encode(content)});
 add('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+sheets.map((s,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')+'</Types>');
 add('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
 add('xl/workbook.xml','<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheets.map((s,i)=>`<sheet name="${xml(s.name.slice(0,31))}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')+'</sheets></workbook>');
 add('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+sheets.map((s,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')+'</Relationships>');
 sheets.forEach((sheet,i)=>{
  const rows=sheet.rows;
  const widths=(sheet.widths||rows[0]?.map(()=>22)||[]).map((width,j)=>`<col min="${j+1}" max="${j+1}" width="${width}" customWidth="1"/>`).join('');
  const content=rows.map((row,r)=>'<row r="'+(r+1)+'">'+row.map((value,c)=>typeof value==='number'&&Number.isFinite(value)?`<c r="${col(c)}${r+1}"><v>${value}</v></c>`:`<c r="${col(c)}${r+1}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`).join('')+'</row>').join('');
  add(`xl/worksheets/sheet${i+1}.xml`,'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+widths+'</cols><sheetData>'+content+'</sheetData>'+((rows.length&&rows[0].length)?`<autoFilter ref="A1:${col(rows[0].length-1)}${rows.length}"/>`:'')+'</worksheet>');
 });
 const crcTable=Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=(n&1)?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
 const crc=data=>{let c=0xffffffff;for(const b of data)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0;};
 const u16=n=>[n&255,(n>>>8)&255],u32=n=>[...u16(n),...u16(n>>>16)],chunks=[],central=[];let offset=0,centralLength=0;
 for(const file of files){
  const checksum=crc(file.data),size=file.data.length;
  const local=new Uint8Array([...u32(0x04034b50),...u16(20),...u16(0x800),...u16(0),...u16(0),...u16(33),...u32(checksum),...u32(size),...u32(size),...u16(file.name.length),...u16(0),...file.name]);
  const directory=new Uint8Array([...u32(0x02014b50),...u16(20),...u16(20),...u16(0x800),...u16(0),...u16(0),...u16(33),...u32(checksum),...u32(size),...u32(size),...u16(file.name.length),...u16(0),...u16(0),...u16(0),...u16(0),...u32(0),...u32(offset),...file.name]);
  chunks.push(local,file.data);central.push(directory);offset+=local.length+size;centralLength+=directory.length;
 }
 const end=new Uint8Array([...u32(0x06054b50),...u16(0),...u16(0),...u16(files.length),...u16(files.length),...u32(centralLength),...u32(offset),...u16(0)]);
 return new Blob([...chunks,...central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
};
