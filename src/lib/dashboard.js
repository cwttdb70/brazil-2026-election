import { dynamicEnglish, portuguese } from './i18n.js';

const KEYS = ['total', 'before17', 'between17and19', 'after19', 'unclassified', 'expectedSections', 'analyzedSections', 'excludedSections', 'observedTotal', 'observedUnclassified'];
const REGIONS = ['north', 'northeast', 'southeast', 'south', 'central-west', 'overseas'];
const PALETTE = { green: '#376d59', amber: '#d6a35e', grey: '#a5b4ad', grid: '#edf0e8', ink: '#234b3d', muted: '#92a087' };
const STATE_CENTERS = { AC:[-70,-9],AL:[-36.5,-9.7],AP:[-51.8,1.1],AM:[-64,-4.3],BA:[-41.5,-12.3],CE:[-39.5,-5.1],DF:[-47.8,-15.8],ES:[-40.6,-19.6],GO:[-49.6,-16.2],MA:[-45,-5],MT:[-55.8,-13],MS:[-54.6,-20.3],MG:[-45,-18.5],PA:[-52.5,-4],PB:[-36.8,-7.2],PR:[-51.8,-24.7],PE:[-37.5,-8.5],PI:[-42.8,-7.2],RJ:[-42.6,-22.3],RN:[-36.7,-5.6],RS:[-53.3,-30],RO:[-63,-11.1],RR:[-61.5,2],SC:[-50.3,-27.3],SP:[-48.8,-22.2],SE:[-37.5,-10.6],TO:[-48.5,-10] };

export function initDashboard() {
 const $ = (id) => document.getElementById(id);
 const en = { ...dynamicEnglish };
 document.querySelectorAll('[data-i18n]').forEach((el) => { en[el.dataset.i18n] = el.innerText.trim().replace(/\s+/g, ' '); });
 en.coverageTitle = 'Data coverage and reconciliation';
 const dictionary = { en, 'pt-BR': portuguese };
 let storedLanguage;
 try { storedLanguage = localStorage.getItem('brazil-2026-language'); } catch {}
 const state = {
  language: storedLanguage === 'en' || storedLanguage === 'pt-BR' ? storedLanguage : (navigator.language.toLowerCase().startsWith('pt') ? 'pt-BR' : 'en'),
  scope: 'all', region: '', uf: '', city: '', chart: 'volume', interval: 15, zoom: false,
  table: 'states', search: '', sort: 'total', descending: true, page: 0
 };
 let data = null, geo = null, official = null, reconciliation = null, currentRows = [], visibleTableRows = [], destroyed = false;
 let chartFrame, toastTimer, refreshTimer, tableDebounce;
 const controller = new AbortController();
 const t = (key) => dictionary[state.language][key] ?? en[key] ?? key;
 const locale = () => state.language === 'pt-BR' ? 'pt-BR' : 'en-GB';
 const fmt = (n) => new Intl.NumberFormat(locale()).format(Number(n) || 0);
 const compact = (n) => new Intl.NumberFormat(locale(), { notation:'compact', maximumFractionDigits:1 }).format(Number(n) || 0);
 const percent = (n, total = 1) => new Intl.NumberFormat(locale(), { style:'percent', minimumFractionDigits:1, maximumFractionDigits:2 }).format(total ? n / total : 0);
 const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
 const cleanName = (s) => {
  if (s === s.toLocaleUpperCase('pt-BR')) return s.toLocaleLowerCase('pt-BR').replace(/(^|[\s-])\p{L}/gu, (c) => c.toLocaleUpperCase('pt-BR')).replace(/\b(De|Do|Da|Dos|Das|E)\b/g, (c) => c.toLowerCase());
  return s;
 };
 const regionName = (id) => t(id);
 const setText = (id, value) => { const el = $(id); if (el) el.textContent = value; };
 const sum = (rows) => {
  const result = Object.fromEntries(KEYS.map((k) => [k, 0]));
  result.histogram = Array(288).fill(0);
  for (const row of rows) {
   for (const key of KEYS) result[key] += Number(row[key]) || 0;
   for (let i = 0; i < 288; i++) result.histogram[i] += Number(row.histogram?.[i]) || 0;
  }
  return result;
 };
 const matchesScope = (row) => state.scope === 'all' || (state.scope === 'overseas' ? row.isOverseas : !row.isOverseas);
 const scopedRows = () => data.municipalities.filter(matchesScope);
 const selectedRows = () => scopedRows().filter((r) => (!state.region || r.region === state.region) && (!state.uf || r.uf === state.uf) && (!state.city || r.id === state.city));
 function aggregateBy(rows, field) {
  const groups = new Map();
  for (const row of rows) {
   const id = row[field];
   if (!groups.has(id)) groups.set(id, []);
   groups.get(id).push(row);
  }
  return [...groups.entries()].map(([id, values]) => {
   const source = field === 'uf' ? data.states.find((r) => r.uf === id) : data.regions.find((r) => r.id === id);
   return { ...source, id: field === 'uf' ? String(id).toLowerCase() : id, uf: field === 'uf' ? id : undefined, name: source?.name || id, ...sum(values) };
  });
 }
 function validate(snapshot) {
  if (!Array.isArray(snapshot.municipalities) || snapshot.intervalMinutes !== 5) throw new Error('Invalid snapshot');
  for (const row of snapshot.municipalities) {
   if (row.total !== row.before17 + row.between17and19 + row.after19 + row.unclassified) throw new Error('Bucket reconciliation');
   if (!Array.isArray(row.histogram) || row.histogram.length !== 288) throw new Error('Histogram schema');
   if (row.histogram.some((n) => !Number.isSafeInteger(n) || n < 0)) throw new Error('Invalid bin');
   if (row.histogram.reduce((a,b) => a+b,0) + row.unclassified !== row.total) throw new Error('Histogram reconciliation');
  }
  const domestic = sum(snapshot.municipalities.filter((r) => !r.isOverseas));
  for (const key of ['total','before17','between17and19','after19','unclassified']) if (domestic[key] !== snapshot.totals[key]) throw new Error('National reconciliation');
 }
 function translate() {
  document.documentElement.lang = state.language;
  document.title = state.language === 'pt-BR' ? 'Análise das Eleições de 2026 no Brasil' : 'Brazil 2026 Election Analysis';
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-language]').forEach((el) => { el.setAttribute('aria-pressed', String(el.dataset.language === state.language)); });
  const isPt = state.language === 'pt-BR';
  document.querySelector('nav').setAttribute('aria-label', isPt ? 'Navegação principal' : 'Main navigation');
  document.querySelector('.language-switch').setAttribute('aria-label', isPt ? 'Idioma' : 'Language');
  $('previous-page').setAttribute('aria-label', isPt ? 'Página anterior' : 'Previous page');
  $('next-page').setAttribute('aria-label', isPt ? 'Próxima página' : 'Next page');
  $('map-reset').title = isPt ? 'Limpar seleção do mapa' : 'Reset map selection';
  $('map-reset').setAttribute('aria-label', $('map-reset').title);
  const brand=document.querySelector('.brand>span');if(brand?.firstChild)brand.firstChild.textContent=isPt?'BRASIL ':'BRAZIL ';
  const footer=document.querySelector('.footer-brand');if(footer?.firstChild)footer.firstChild.textContent=isPt?'BRASIL ':'BRAZIL ';
  if (data) render();
 }
 function updateOptions() {
  function options(el, rows, current, label) {
   const fragment = document.createDocumentFragment();
   const first = document.createElement('option'); first.value = ''; first.textContent = label; fragment.append(first);
   for (const [value, name] of rows) { const opt = document.createElement('option'); opt.value = value; opt.textContent = name; fragment.append(opt); }
   el.replaceChildren(fragment); el.value = current;
  }
  const scopeRows = scopedRows();
  const regionIds = new Set(scopeRows.map((r) => r.region));
  options($('region-filter'), REGIONS.filter((id) => regionIds.has(id)).map((id) => [id, regionName(id)]), state.region, t('allRegions'));
  const states = data.states.filter((s) => matchesScope(s) && (!state.region || s.region === state.region)).sort((a,b) => a.name.localeCompare(b.name, locale()));
  options($('state-filter'), states.map((s) => [s.uf, cleanName(s.name)]), state.uf, t('allStates'));
  const cities = scopeRows.filter((r) => (!state.region || r.region === state.region) && (!state.uf || r.uf === state.uf)).sort((a,b) => a.name.localeCompare(b.name, locale()));
  options($('city-filter'), cities.map((c) => [c.id, cleanName(c.name) + ' · ' + c.uf]), state.city, t('allCities'));
  $('scope-filter').value = state.scope;
 }
 function selectionName() {
  if (state.city) { const c = data.municipalities.find((r) => r.id === state.city); return cleanName(c.name) + ' · ' + c.uf; }
  if (state.uf) return cleanName(data.states.find((r) => r.uf === state.uf)?.name || state.uf);
  if (state.region) return regionName(state.region);
  return t(state.scope === 'all' ? 'worldwideLabel' : state.scope === 'overseas' ? 'overseas' : 'brazil');
 }
 function render() {
  if (!data || destroyed) return;
  updateOptions();
  currentRows = selectedRows();
  const totals = sum(currentRows), coverage = totals.expectedSections ? totals.analyzedSections / totals.expectedSections : 0;
  setText('selection-label', selectionName());
  setText('total-value', fmt(totals.total)); setText('before-value', fmt(totals.before17)); setText('extension-value', fmt(totals.between17and19));
  setText('after-value', fmt(totals.after19)); setText('unknown-value', fmt(totals.unclassified));
  setText('before-percent', percent(totals.before17, totals.total)); setText('extension-percent', percent(totals.between17and19, totals.total));
  setText('total-note', selectionName() + ' · ' + t('officialTotalNote'));
  setText('coverage-status', t(data.status === 'complete' ? 'complete' : 'partial') + ' · ' + percent(totals.analyzedSections, totals.expectedSections) + ' ' + t('sectionCoverage'));
  setText('coverage-detail', fmt(totals.analyzedSections) + ' / ' + fmt(totals.expectedSections) + ' ' + t('sections') + ' ' + t('analysed'));
  if(data._countReconciled){setText('coverage-status',t('complete')+' · '+percent(1)+' '+t('ballotAccounting'));setText('coverage-detail',fmt(totals.total-totals.unclassified)+' / '+fmt(totals.total)+' '+t('withClassifiedTime')+' ('+percent(totals.total-totals.unclassified,totals.total)+')');}
  const overseasUntimed=currentRows.filter((r)=>r.isOverseas).reduce((n,r)=>n+r.total,0),clockUntimed=currentRows.filter((r)=>!r.isOverseas).reduce((n,r)=>n+r.observedUnclassified,0),bulletinUntimed=currentRows.filter((r)=>!r.isOverseas).reduce((n,r)=>n+r.total-r.observedTotal,0);
  $('unknown-value').title=t('overseas')+': '+fmt(overseasUntimed)+'; '+t('clockUncertainty')+': '+fmt(clockUntimed)+'; '+t('bulletinOnly')+': '+fmt(bulletinUntimed);
  setText('coverage-ratio', percent(totals.analyzedSections, totals.expectedSections));
  setText('coverage-included', fmt(totals.analyzedSections));
  setText('coverage-remaining', fmt(Math.max(0, totals.expectedSections - totals.analyzedSections - totals.excludedSections)));
  setText('coverage-flagged', fmt(totals.excludedSections));
  $('coverage-track-fill').style.width = Math.min(100, coverage * 100) + '%';
  const date = new Intl.DateTimeFormat(locale(), {dateStyle:'medium',timeStyle:'short',timeZone:'America/Sao_Paulo'}).format(new Date(data.updatedAt));
  setText('snapshot-date', t('updated') + ' ' + date + ' · Brasília');
  $('zoom-chart').textContent = t(state.zoom ? 'fullDay' : 'zoomWindow');
  document.querySelectorAll('[data-chart]').forEach((el) => el.classList.toggle('active', el.dataset.chart === state.chart));
  document.querySelectorAll('[data-table]').forEach((el) => el.classList.toggle('active', el.dataset.table === state.table));
  renderTimeline(totals); renderMap(); renderRegions(); renderTable(); renderOfficial(totals); renderNationalResult();
  window.__BRAZIL_DASHBOARD__ = { scope:state.scope, language:state.language, totals:{...totals,histogram:undefined}, dataUpdatedAt:data.updatedAt, accountingOK:totals.total===totals.before17+totals.between17and19+totals.after19+totals.unclassified };
 }
 function renderTimeline(totals) {
  const host = $('timeline-chart'), width = Math.max(320, host.clientWidth - 30), height = host.clientHeight || 305;
  const margin = {left:48,right:23,top:52,bottom:36}, pw = width-margin.left-margin.right, ph = height-margin.top-margin.bottom;
  let start = state.zoom ? 16*60 : 8*60, end = state.zoom ? 20*60 : 20*60;
  const occupied = totals.histogram.map((v,i) => v ? i*5 : null).filter((v) => v !== null);
  if (!state.zoom && occupied.length) { start=Math.max(0,Math.min(8*60,Math.floor(occupied[0]/60)*60)); end=Math.min(1440,Math.max(20*60,Math.ceil((occupied[occupied.length-1]+5)/60)*60)); }
  const step=state.interval, bins=[];
  let cumulative=totals.histogram.slice(0,start/5).reduce((a,b)=>a+b,0);
  for(let minute=start;minute<end;minute+=step) {
   const volume=totals.histogram.slice(minute/5,Math.min(288,(minute+step)/5)).reduce((a,b)=>a+b,0);
   cumulative+=volume;bins.push({minute,volume,value:state.chart==='cumulative'?cumulative:volume});
  }
  const max=Math.max(1,...bins.map((b)=>b.value))*1.12, x=(minute)=>margin.left+(minute-start)/(end-start)*pw, y=(v)=>margin.top+ph-v/max*ph;
  const hour=(minute)=>String(Math.floor(minute/60)).padStart(2,'0')+':'+String(minute%60).padStart(2,'0');
  let svg='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="'+escape(t('timelineCopy'))+'"><defs><linearGradient id="area-shade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="'+PALETTE.green+'" stop-opacity=".22"/><stop offset="1" stop-color="'+PALETTE.green+'" stop-opacity=".025"/></linearGradient></defs>';
  svg+='<rect x="'+x(17*60)+'" y="'+margin.top+'" width="'+(x(19*60)-x(17*60))+'" height="'+ph+'" fill="#fcf3e2"/>';
  for(let i=0;i<=4;i++){const value=max*i/4, yy=y(value);svg+='<line x1="'+margin.left+'" y1="'+yy+'" x2="'+(width-margin.right)+'" y2="'+yy+'" stroke="'+PALETTE.grid+'" stroke-dasharray="3 4"/><text x="'+(margin.left-10)+'" y="'+(yy+3)+'" text-anchor="end" fill="'+PALETTE.muted+'" font-size="8">'+escape(compact(value))+'</text>';}
  const tickStep=width<500?(state.zoom?60:120):60;
  for(let minute=start;minute<=end;minute+=tickStep)svg+='<text x="'+x(minute)+'" y="'+(height-14)+'" text-anchor="middle" fill="'+PALETTE.muted+'" font-size="8">'+hour(minute)+'</text>';
  if(state.chart==='cumulative'){
   const points=bins.map((b)=>[x(b.minute+step/2),y(b.value)]);
   const line=points.map((p,i)=>(i?'L':'M')+p[0].toFixed(2)+','+p[1].toFixed(2)).join('');
   svg+='<path d="'+line+'L'+x(end)+','+(margin.top+ph)+'L'+x(start)+','+(margin.top+ph)+'Z" fill="url(#area-shade)"/><path d="'+line+'" fill="none" stroke="'+PALETTE.green+'" stroke-width="2.2" stroke-linejoin="round"/>';
  }
  bins.forEach((bin,i)=>{
   const xx=x(bin.minute)+.6,bw=Math.max(.5,x(bin.minute+step)-x(bin.minute)-1.2),hh=bin.volume?Math.max(.8,margin.top+ph-y(bin.value)):0;
   const color=bin.minute<1020?PALETTE.green:bin.minute<1140?PALETTE.amber:PALETTE.grey;
   if(state.chart==='volume')svg+='<rect class="plot-bar" x="'+xx+'" y="'+(margin.top+ph-hh)+'" width="'+bw+'" height="'+hh+'" rx="1" fill="'+color+'"/>';
   svg+='<rect class="chart-hit" data-bin="'+i+'" x="'+x(bin.minute)+'" y="'+margin.top+'" width="'+Math.max(1,x(bin.minute+step)-x(bin.minute))+'" height="'+ph+'" fill="transparent"/>';
  });
  for(const [minute,label,color] of [[1020,t('originalClose'),PALETTE.green],[1140,t('extendedClose'),'#ac7d3c']]){
   const xx=x(minute),nearRight=width-xx<110;
   svg+='<line x1="'+xx+'" y1="'+(margin.top-7)+'" x2="'+xx+'" y2="'+(margin.top+ph)+'" stroke="'+color+'" stroke-width="1" stroke-dasharray="4 3"/><text x="'+(xx+(nearRight?-6:6))+'" y="22" text-anchor="'+(nearRight?'end':'start')+'" fill="'+color+'" font-size="8" font-weight="600">'+escape(label)+'</text><text x="'+(xx+(nearRight?-6:6))+'" y="36" text-anchor="'+(nearRight?'end':'start')+'" fill="'+color+'" font-size="10" font-weight="600">'+hour(minute)+'</text>';
  }
  if(!occupied.length)svg+='<text x="'+(margin.left+pw/2)+'" y="'+(margin.top+ph/2)+'" text-anchor="middle" class="chart-empty">'+escape(t('noBallots'))+'</text>';
  host.innerHTML=svg+'</svg>';
  host.querySelectorAll('[data-bin]').forEach((el)=>{
   el.addEventListener('pointermove',(event)=>{const b=bins[Number(el.dataset.bin)];showTooltip(event,'<strong>'+hour(b.minute)+'–'+hour(b.minute+step)+'</strong><div class="tip-line"><span>'+escape(t(state.chart==='cumulative'?'cumulative':'ballotsRecorded'))+'</span><b>'+fmt(b.value)+'</b></div><div class="tip-line"><span>Brasília · UTC−3</span></div>');});
   el.addEventListener('pointerleave',hideTooltip);
  });
 }
 function renderMap() {
  if(!geo)return;
  const rows=scopedRows().filter((r)=>!r.isOverseas&&(!state.region||r.region===state.region));
  const states=aggregateBy(rows,'uf'),lookup=new Map(states.map((s)=>[s.uf,s]));
  const values=states.filter((s)=>s.total-s.unclassified>0).map((s)=>(s.between17and19+s.after19)/(s.total-s.unclassified));
  const max=Math.max(.0001,...values);
  const project=([lon,lat])=>[(lon+74)*11.1,(6-lat)*11.7];
  const pathFor=(geometry)=>{
   const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.coordinates;
   return polygons.map((polygon)=>polygon.map((ring)=>ring.map((point,i)=>{const p=project(point);return(i?'L':'M')+p[0].toFixed(2)+','+p[1].toFixed(2);}).join('')+'Z').join('')).join('');
  };
  function color(ratio){
   const stops=[[237,242,221],[178,204,161],[103,151,109],[42,103,86]],v=Math.min(1,Math.max(0,ratio))*3,index=Math.min(2,Math.floor(v)),f=v-index;
   return'rgb('+stops[index].map((c,i)=>Math.round(c+(stops[index+1][i]-c)*f)).join(',')+')';
  }
  let svg='<svg viewBox="-5 -5 520 485" role="group" aria-label="'+escape(t('mapTitle'))+'">';
  for(const feature of geo.features){
   const uf=feature.properties.uf.toUpperCase(),s=lookup.get(uf),timed=s?s.total-s.unclassified:0,ratio=timed?(s.between17and19+s.after19)/timed:0;
   const selected=state.uf===uf,dimmed=state.uf&&state.uf!==uf;
   svg+='<path class="state-path'+(selected?' selected':'')+'" data-uf="'+uf+'" d="'+pathFor(feature.geometry)+'" fill="'+(timed?color(ratio/max):'#e7eae4')+'" opacity="'+(dimmed?.4:1)+'" role="button" tabindex="0" aria-label="'+escape(cleanName(feature.properties.name))+'" aria-pressed="'+selected+'"/>';
  }
  for(const [uf,center] of Object.entries(STATE_CENTERS)){const p=project(center);svg+='<text class="state-label" x="'+p[0]+'" y="'+p[1]+'" opacity="'+(state.uf&&state.uf!==uf?.3:.85)+'">'+uf+'</text>';}
  $('brazil-map').innerHTML=svg+'</svg>';
  setText('map-legend-high',values.length?percent(max):'—');
  $('brazil-map').querySelectorAll('[data-uf]').forEach((el)=>{
   const uf=el.dataset.uf,s=lookup.get(uf);
   const activate=()=>{state.uf=state.uf===uf?'':uf;state.city='';if(state.uf)state.region=data.states.find((r)=>r.uf===state.uf)?.region||'';state.page=0;if(state.scope==='overseas')state.scope='all';hideTooltip();render();};
   el.addEventListener('click',activate);el.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate();}});
   el.addEventListener('pointermove',(event)=>{
    const original=data.states.find((r)=>r.uf===uf),timed=s?s.total-s.unclassified:0;
    showTooltip(event,'<strong>'+escape(cleanName(original?.name||uf))+' · '+uf+'</strong>'+(s&&s.total?'<div class="tip-line"><span>'+escape(t('ballots'))+'</span><b>'+fmt(s.total)+'</b></div><div class="tip-line"><span>'+escape(t('afterFive'))+'</span><b>'+percent(s.between17and19+s.after19,timed)+'</b></div><div class="tip-line"><span>'+escape(t('coverage'))+'</span><b>'+percent(s.analyzedSections,s.expectedSections)+'</b></div>':'<div class="tip-line">'+escape(t('noDataTip'))+'</div>'));
   });el.addEventListener('pointerleave',hideTooltip);
  });
 }
 function renderRegions(){
  const groups=aggregateBy(currentRows,'region'),max=Math.max(1,...groups.map((r)=>r.total)),allTotal=groups.reduce((n,r)=>n+r.total,0);
  const html=REGIONS.filter((id)=>groups.some((g)=>g.id===id)).map((id)=>{
   const r=groups.find((g)=>g.id===id),late=r.between17and19+r.after19;
   return'<button class="region-row'+(state.region===id?' selected':'')+'" data-region="'+id+'"><div><span>'+escape(regionName(id))+'</span><strong>'+fmt(r.total)+'</strong></div><div class="region-track"><div style="width:'+(r.total/max*100)+'%"><span class="late-segment" style="width:'+(r.total?late/r.total*100:0)+'%"></span></div></div><div><span>'+percent(r.total,allTotal)+' '+escape(t('ofAnalysed'))+'</span><span>'+fmt(late)+' '+escape(t('afterFive'))+'</span></div></button>';
  }).join('');
  $('region-bars').innerHTML=html||'<p class="loading-placeholder">'+escape(t('noBallots'))+'</p>';
  $('region-bars').querySelectorAll('[data-region]').forEach((el)=>el.addEventListener('click',()=>{state.region=state.region===el.dataset.region?'':el.dataset.region;state.uf='';state.city='';state.page=0;render();}));
 }
 function renderTable(){
  let rows=state.table==='states'?aggregateBy(currentRows,'uf'):currentRows.map((r)=>({...r,name:cleanName(r.name)}));
  const normalized=(s)=>String(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  if(state.search)rows=rows.filter((r)=>normalized(r.name+' '+r.uf).includes(normalized(state.search)));
  rows.sort((a,b)=>{
   let av,bv;
   if(state.sort==='name'){av=a.name;bv=b.name;return av.localeCompare(bv,locale())*(state.descending?-1:1);}
   if(state.sort==='coverage'){av=a.expectedSections?a.analyzedSections/a.expectedSections:0;bv=b.expectedSections?b.analyzedSections/b.expectedSections:0;}
   else{av=a[state.sort]||0;bv=b[state.sort]||0;}
   return(av-bv)*(state.descending?-1:1)||a.name.localeCompare(b.name,locale());
  });
  visibleTableRows=rows;
  const pageSize=12,pages=Math.max(1,Math.ceil(rows.length/pageSize));state.page=Math.min(state.page,pages-1);
  const start=state.page*pageSize,pageRows=rows.slice(start,start+pageSize);
  $('detail-table').innerHTML=pageRows.map((r)=>'<tr><td><button class="table-place" data-place="'+escape(state.table==='states'?r.uf:r.id)+'">'+escape(cleanName(r.name))+'</button>'+(state.table==='municipalities'?'<span class="place-uf">'+escape(r.uf)+'</span>':'')+'</td><td class="cell-total">'+fmt(r.total)+'</td><td>'+fmt(r.before17)+'</td><td class="extension-column">'+fmt(r.between17and19)+'<span class="cell-sub">'+percent(r.between17and19,r.total)+'</span></td><td>'+fmt(r.after19)+'</td><td>'+fmt(r.unclassified)+'</td><td><span class="coverage-cell">'+percent(r.analyzedSections,r.expectedSections)+'<i><span style="width:'+Math.min(100,r.expectedSections?r.analyzedSections/r.expectedSections*100:0)+'%"></span></i></span></td></tr>').join('')||'<tr><td colspan="7" class="empty-row">'+escape(t('noResults'))+'</td></tr>';
  $('detail-table').querySelectorAll('[data-place]').forEach((el)=>el.addEventListener('click',()=>{
   if(state.table==='states'){state.uf=el.dataset.place;state.city='';}
   else{const r=data.municipalities.find((m)=>m.id===el.dataset.place);state.city=r.id;state.uf=r.uf;state.region=r.region;}
   state.page=0;render();$('analysis').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }));
  setText('table-count',fmt(rows.length)+' '+t('places'));
  setText('table-range',rows.length?t('showing')+' '+fmt(start+1)+'–'+fmt(Math.min(start+pageSize,rows.length))+' '+t('of')+' '+fmt(rows.length):t('noResults'));
  setText('page-indicator',fmt(state.page+1)+' / '+fmt(pages));$('previous-page').disabled=state.page===0;$('next-page').disabled=state.page>=pages-1;
  document.querySelectorAll('[data-sort]').forEach((el)=>{el.classList.toggle('sort-active',el.dataset.sort===state.sort);el.querySelector('.sort-icon').textContent=el.dataset.sort===state.sort?(state.descending?'↓':'↑'):'↕';});
 }
 function renderOfficial(totals){
  if(!official)return;
  const ids=new Set(currentRows.map((r)=>r.id));
  const matching=official.municipalities.filter((r)=>ids.has(r.id));
  const benchmark=matching.reduce((n,r)=>n+r.officialTurnout,0);
  setText('official-value',fmt(benchmark));setText('official-analysed',fmt(totals.observedTotal));setText('official-gap',fmt(benchmark-totals.observedTotal));$('official-gap').classList.toggle('overcount',benchmark<totals.observedTotal);
  $('official-source').href=official.sourceUrl;
 }

 function applyOfficialTotals(snapshot){
  if(!official)throw new Error('Official benchmark unavailable');
  const lookup=new Map(official.municipalities.map((r)=>[r.id,r]));
  const countReconciled=reconciliation?.national?.residual===0&&reconciliation.national.observed_log_total===snapshot.allTotals.total&&reconciliation.analysisUpdatedAt===snapshot.updatedAt;
  return {...snapshot,_countReconciled:countReconciled,municipalities:snapshot.municipalities.map((row)=>{
   const benchmark=lookup.get(row.id);if(!benchmark)throw new Error('Official municipality missing: '+row.id);
   const timed=row.before17+row.between17and19+row.after19;
   const result={...row,observedTotal:row.total,observedUnclassified:row.unclassified,total:benchmark.officialTurnout,expectedSections:benchmark.sectionsInstalled,unclassified:benchmark.officialTurnout-timed};
   if(result.unclassified<0){result.before17=0;result.between17and19=0;result.after19=0;result.histogram=Array(288).fill(0);result.unclassified=result.total;result.timingAccountingFlag=true;}
   if(result.total!==result.before17+result.between17and19+result.after19+result.unclassified)throw new Error('Official display reconciliation');
   return result;
  })};
 }
 function renderNationalResult(){
  if(!official)return;
  const result=official.national.totals,candidates=official.national.candidates;
  const lula=candidates.find((c)=>c.number==='13'),flavio=candidates.find((c)=>c.number==='22');
  setText('national-ballots',fmt(result.totalBallots));setText('lula-votes',fmt(lula.votes));setText('flavio-votes',fmt(flavio.votes));setText('lula-foot',fmt(lula.votes));setText('flavio-foot',fmt(flavio.votes));setText('blank-votes',fmt(result.blankVotes));setText('null-votes',fmt(result.nullVotes));
  setText('lula-percent',percent(lula.votes,result.validVotes));setText('flavio-percent',percent(flavio.votes,result.validVotes));$('lula-bar').style.width=(lula.votes/result.validVotes*100)+'%';$('flavio-bar').style.width=(flavio.votes/result.validVotes*100)+'%';
  const values=[lula.votes,flavio.votes,result.blankVotes,result.nullVotes];document.querySelectorAll('.ballot-composition>div').forEach((el,i)=>el.style.width=(values[i]/result.totalBallots*100)+'%');
 }
 function showTooltip(event,html){
  const tip=$('tooltip');tip.innerHTML=html;tip.hidden=false;
  const width=tip.offsetWidth,height=tip.offsetHeight;
  tip.style.left=Math.max(8,Math.min(innerWidth-width-8,event.clientX+15))+'px';
  tip.style.top=Math.max(8,Math.min(innerHeight-height-8,event.clientY+15))+'px';
 }
 function hideTooltip(){$('tooltip').hidden=true;}
 function reset(){state.scope='all';state.region='';state.uf='';state.city='';state.page=0;render();}
 const on=(el,type,fn)=>el.addEventListener(type,fn,{signal:controller.signal});
 on($('scope-filter'),'change',(e)=>{state.scope=e.target.value;state.region='';state.uf='';state.city='';state.page=0;render();});
 on($('region-filter'),'change',(e)=>{state.region=e.target.value;state.uf='';state.city='';state.page=0;render();});
 on($('state-filter'),'change',(e)=>{state.uf=e.target.value;state.city='';state.page=0;render();});
 on($('city-filter'),'change',(e)=>{state.city=e.target.value;if(state.city){const r=data.municipalities.find((m)=>m.id===state.city);state.uf=r.uf;state.region=r.region;}state.page=0;render();});
 on($('reset-filters'),'click',reset);on($('map-reset'),'click',reset);
 on($('interval-filter'),'change',(e)=>{state.interval=Number(e.target.value);renderTimeline(sum(currentRows));});
 on($('zoom-chart'),'click',()=>{state.zoom=!state.zoom;render();});
 document.querySelectorAll('[data-chart]').forEach((el)=>on(el,'click',()=>{state.chart=el.dataset.chart;render();}));
 document.querySelectorAll('[data-table]').forEach((el)=>on(el,'click',()=>{state.table=el.dataset.table;state.page=0;renderTable();document.querySelectorAll('[data-table]').forEach((b)=>b.classList.toggle('active',b.dataset.table===state.table));}));
 document.querySelectorAll('[data-sort]').forEach((el)=>on(el,'click',()=>{if(state.sort===el.dataset.sort)state.descending=!state.descending;else{state.sort=el.dataset.sort;state.descending=state.sort!=='name';}state.page=0;renderTable();}));
 document.querySelectorAll('[data-language]').forEach((el)=>on(el,'click',()=>{state.language=el.dataset.language;try{localStorage.setItem('brazil-2026-language',state.language);}catch{}translate();}));
 on($('table-search'),'input',(e)=>{state.search=e.target.value;state.page=0;clearTimeout(tableDebounce);tableDebounce=setTimeout(renderTable,100);});
 on($('previous-page'),'click',()=>{if(state.page>0){state.page--;renderTable();}});on($('next-page'),'click',()=>{state.page++;renderTable();});
 on($('download-csv'),'click',()=>{
  const columns=[['name',t('place')],['uf',t('state')],['total',t('ballots')],['before17',t('beforeShort')],['between17and19',t('extensionShort')],['after19',t('afterShort')],['unclassified',t('untimed')],['analyzedSections',t('analysedSections')],['expectedSections',t('sections')]];
  const cell=(v)=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const csv='\uFEFF'+columns.map(([,label])=>cell(label)).join(',')+'\r\n'+visibleTableRows.map((r)=>columns.map(([key])=>cell(r[key])).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='brazil-2026-round-2-'+state.table+'-'+state.language+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  $('toast').textContent=t('csvReady');$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,2500);
 });
 if($('copy-command'))on($('copy-command'),'click',async()=>{try{await navigator.clipboard.writeText('python3 reproduce.py --database brazil-2026.sqlite --output results.json');$('toast').textContent=t('copied');$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,2000);}catch{}});
 const resize=new ResizeObserver(()=>{cancelAnimationFrame(chartFrame);chartFrame=requestAnimationFrame(()=>{if(data)renderTimeline(sum(currentRows));});});resize.observe($('timeline-chart'));
 translate();
 async function load(){
  try{
   const [response,mapResponse,officialResponse,reconciliationResponse]=await Promise.all([fetch('/data/summary.json',{cache:'no-store',signal:controller.signal}),fetch('/assets/brazil-states.geojson',{signal:controller.signal}),fetch('/data/official-results.json',{cache:'no-store',signal:controller.signal}),fetch('/data/reconciliation.json',{cache:'no-store',signal:controller.signal})]);
   if(!response.ok||!officialResponse.ok)throw new Error('Snapshot unavailable');
   official=await officialResponse.json();if(reconciliationResponse.ok)reconciliation=await reconciliationResponse.json();if(mapResponse.ok)geo=await mapResponse.json();
   const snapshot=await response.json();validate(snapshot);if(destroyed)return;data=applyOfficialTotals(snapshot);render();
  }catch(error){
   if(error.name==='AbortError')return;
   setText('coverage-status',t('loadError'));$('data-banner').classList.add('error-state');$('timeline-chart').innerHTML='<div class="loading-placeholder">'+escape(t('loadError'))+'</div>';console.error('Dashboard aggregate loading failed:',error.message);
  }
 }

 load();
 // Visitors can receive a newer deployed snapshot without losing their filter selection.
 refreshTimer=setInterval(async()=>{if(!data||document.hidden)return;try{const r=await fetch('/data/summary.json',{cache:'no-store',signal:controller.signal});if(!r.ok)return;const next=await r.json();if(next.updatedAt!==data.updatedAt){validate(next);data=applyOfficialTotals(next);render();}}catch{}},120000);
 return()=>{destroyed=true;controller.abort();resize.disconnect();cancelAnimationFrame(chartFrame);clearTimeout(toastTimer);clearTimeout(tableDebounce);clearInterval(refreshTimer);delete window.__BRAZIL_DASHBOARD__;};
}
