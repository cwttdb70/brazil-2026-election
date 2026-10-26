#!/usr/bin/env python3
"""Incrementally derive public aggregates from local TSE logs. Never exports raw logs.
Only this tool's aggregate JSON/CSV outputs are suitable for publication.
"""
from __future__ import annotations
import argparse,collections,concurrent.futures as cf,datetime as dt,hashlib,io,json,multiprocessing,os,sqlite3,time,zipfile
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]
RAW=BASE/'data/second-round/databases'
ANALYSIS=BASE/'data/analysis'
PUBLIC=BASE/'website/data'
DATE='25/10/2026';BINS=288
UF_INFO={
'ac':('Acre','north'),'al':('Alagoas','northeast'),'am':('Amazonas','north'),'ap':('Amapá','north'),
'ba':('Bahia','northeast'),'ce':('Ceará','northeast'),'df':('Distrito Federal','central-west'),'es':('Espírito Santo','southeast'),
'go':('Goiás','central-west'),'ma':('Maranhão','northeast'),'mg':('Minas Gerais','southeast'),'ms':('Mato Grosso do Sul','central-west'),
'mt':('Mato Grosso','central-west'),'pa':('Pará','north'),'pb':('Paraíba','northeast'),'pe':('Pernambuco','northeast'),
'pi':('Piauí','northeast'),'pr':('Paraná','south'),'rj':('Rio de Janeiro','southeast'),'rn':('Rio Grande do Norte','northeast'),
'ro':('Rondônia','north'),'rr':('Roraima','north'),'rs':('Rio Grande do Sul','south'),'sc':('Santa Catarina','south'),
'se':('Sergipe','northeast'),'sp':('São Paulo','southeast'),'to':('Tocantins','north'),'zz':('Overseas','overseas')}
REGIONS={'north':'North','northeast':'Northeast','central-west':'Central-West','southeast':'Southeast','south':'South','overseas':'Overseas'}
AM_UTC5={'02917','02054','02135','02291','02313','02020','02399','98370','02496','02755','98477'}
SOURCES=[
{'title':'TSE: second-round voting-machine archives','url':'https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/br/cargo/1/vis/nominal/dados-de-urna/log-da-urna'},
{'title':'TSE: 2026 log format and archived log segments','url':'https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/log-do-ecossistema'},
{'title':'TSE: nationwide Brasília polling schedule and local time zones','url':'https://www.tse.jus.br/comunicacao/noticias/2026/Setembro/faltam-26-dias-votacao-comeca-e-termina-no-mesmo-horario-em-todo-o-pais'},
{'title':'TRE-AM: eleven municipalities on Acre time','url':'https://www.tre-am.jus.br/comunicacao/noticias/2026/Setembro/eleitorado-do-amazonas-deve-ficar-atento-aos-horarios-de-votacao-nas-eleicoes-2026'},
{'title':'TSE: extension clarification, 25 October','url':'https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/esclarecimento-sobre-prorrogacao-da-votacao'},
{'title':'Resolution 23.751/2026: partial voting and automatic null ballots (art.144)','url':'https://www.tse.jus.br/legislacao/compilada/res/2026/resolucao-no-23-751-de-26-de-fevereiro-de-2026'},
{'title':'TSE: voters already in line at 17:00 may vote afterward','url':'https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/o-que-acontece-nas-secoes-eleitorais-apos-as-17h-entenda-as-regras-de-seguranca-e-fiscalizacao'}]
METRIC_FIELDS=['total','before17','between17and19','after19','unclassified']
def zero():return dict(total=0,before17=0,between17and19=0,after19=0,unclassified=0,histogram=[0]*BINS)
def add(target,source,sign=1):
 for k in METRIC_FIELDS:target[k]+=sign*source[k]
 for i,n in enumerate(source['histogram']):target['histogram'][i]+=sign*n

def shift_minutes(uf,municipality):
 if uf=='zz':return None
 if uf=='ac' or (uf=='am' and municipality in AM_UTC5):return 120
 if uf in {'am','ro','rr','mt','ms'}:return 60
 if uf=='pe' and municipality=='30015':return -60
 return 0

def package_lines(body,metrics,depth=0,target=None):
 if depth>12:raise ValueError('archive_depth_limit')
 with zipfile.ZipFile(io.BytesIO(body)) as archive:
  infos=archive.infolist()
  if sum(i.file_size for i in infos)>100*1024**2:raise ValueError('archive_size_limit')
  for info in infos:
   name=info.filename
   if name.endswith(('.jez','.zip')):
    metrics['archivedPackages']+=1
    yield from package_lines(archive.read(name),metrics,depth+1,target)
   elif name.endswith(('.dat','.log')):
    metrics['textSegments']+=1
    previous=None;context={'municipality':None,'zone':None,'section':None}
    for line in archive.read(name).decode('latin-1').splitlines():
     parts=line.split('\t')
     if len(parts)>=5:
      message=parts[4]
      for prefix,key,width in [('Município: ','municipality',5),('Zona Eleitoral: ','zone',4),('Seção Eleitoral: ','section',4)]:
       if message.startswith(prefix):context[key]=message[len(prefix):].strip().zfill(width)
     if target and all(context.values()) and tuple(context[k] for k in ['municipality','zone','section'])!=target:
      if line.startswith(DATE) and len(parts)>=5 and parts[3]=='VOTA':metrics['foreignSectionRowsSkipped']+=1
      continue
     if line.startswith(DATE):
      try:
       hour,minute,second=map(int,line[11:19].split(':'));seconds=hour*3600+minute*60+second
       if previous is not None and seconds<previous-120:metrics['clockBacksteps']+=1
       previous=seconds
      except ValueError:pass
     yield line

def parse_log(body,uf,municipality,zone=None,section=None):
 metrics=collections.Counter();out=zero();seen=set();completed=0;confirmed=0;nulls=0;vota=0;shift=shift_minutes(uf,municipality)
 for line in package_lines(body,metrics,target=(municipality,zone,section) if zone and section else None):
  if not line.startswith(DATE):continue
  if line in seen:metrics['duplicateRows']+=1;continue
  seen.add(line);parts=line.split('\t')
  if len(parts)<6 or parts[3]!='VOTA':continue
  vota+=1;msg=parts[4]
  if msg=='O voto do eleitor foi computado':completed+=1
  if msg=='Voto confirmado para [Presidente]':confirmed+=1
  elif msg=='Atribuido voto nulo por suspensão [Presidente]':nulls+=1
  else:continue
  out['total']+=1
  if shift is None:out['unclassified']+=1;continue
  try:hour,minute,second=map(int,line[11:19].split(':'));seconds=hour*3600+minute*60+second+shift*60
  except ValueError:out['unclassified']+=1;continue
  if not 0<=seconds<86400:out['unclassified']+=1;continue
  out['histogram'][seconds//300]+=1
  if seconds<17*3600:out['before17']+=1
  elif seconds<19*3600:out['between17and19']+=1
  else:out['after19']+=1
 metrics.update(completedSessions=completed,presidentialConfirmations=confirmed,automaticNullBallots=nulls)
 if not vota:return 'excluded','no_second_round_vota_records',zero(),dict(metrics)
 if completed!=confirmed+nulls:return 'excluded','presidential_count_does_not_match_completed_sessions',zero(),dict(metrics)
 if metrics['clockBacksteps']:
  metrics['timingUnavailableReason']='machine_clock_discontinuity'
  out=dict(total=out['total'],before17=0,between17and19=0,after19=0,unclassified=out['total'],histogram=[0]*BINS)
 assert out['total']==out['before17']+out['between17and19']+out['after19']+out['unclassified']
 assert sum(out['histogram'])+out['unclassified']==out['total']
 return 'analyzed',None,out,dict(metrics)

def choose_log(aux_blob,metadata):
 aux=json.loads(aux_blob);final=[];received=[]
 for h in aux.get('hashes',[]):
  status=str(h.get('st','')).lower()
  if not (status.startswith('totalizad') or status.startswith('recebid')):continue
  names={a['nm'] for a in h.get('arq',[]) if a.get('tp')=='log'}
  try:published=dt.datetime.strptime(h['dr']+' '+h['hr'],'%d/%m/%Y %H:%M:%S')
  except (KeyError,ValueError):published=dt.datetime.min
  for log in metadata:
   if log['publication_hash']==h['hash'] and log['name'] in names:
    target=final if status.startswith('totalizad') else received
    target.append((published,log))
 if final:
  latest=max(p for p,l in final);best=[l for p,l in final if p==latest]
  if len({l['sha256'] for l in best})>1:raise ValueError('ambiguous_latest_log_versions')
  selected=dict(best[0]);selected['receivedStatusWarning']=False;return selected
 # Received metadata is retained as a warning, not equated with rejected votes.
 # Only one uniquely published hash/content is eligible; ambiguous versions stay excluded.
 if received and len({(l['publication_hash'],l['sha256']) for _,l in received})==1:
  selected=dict(received[0][1]);selected['receivedStatusWarning']=True;return selected
 if received:raise ValueError('ambiguous_received_log_versions')
 raise ValueError('no_published_vota_log_version')

def checkpoint(uf):
 ANALYSIS.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(ANALYSIS/f'{uf}.sqlite');db.execute('PRAGMA journal_mode=WAL')
 db.executescript('''CREATE TABLE IF NOT EXISTS analyzed(municipality TEXT,zone TEXT,section TEXT,fingerprint TEXT,status TEXT,reason TEXT,stats TEXT,metrics TEXT,PRIMARY KEY(municipality,zone,section));
 CREATE TABLE IF NOT EXISTS municipalities(code TEXT PRIMARY KEY,name TEXT,expected INTEGER,analyzed INTEGER DEFAULT 0,excluded INTEGER DEFAULT 0,stats TEXT);
 CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY,body BLOB);
 CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);''');return db

def analyze_state(task):
 uf,limit,batch=task;src=sqlite3.connect(f'file:{RAW/(uf+".sqlite")}?mode=ro',uri=True);src.row_factory=sqlite3.Row;db=checkpoint(uf)
 blob=src.execute("SELECT body FROM sources WHERE name='section-index'").fetchone()['body'];idx=json.loads(blob)
 db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',('section-index',blob))
 for abr in idx['abr']:
  for m in abr['mu']:
   expected=sum('nsp' not in s for z in m['zon'] for s in z['sec'])
   db.execute('INSERT INTO municipalities(code,name,expected,stats) VALUES(?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,expected=excluded.expected',(m['cd'],m['nm'],expected,json.dumps(zero(),separators=(',',':'))))
 db.commit()
 municipal={r[0]:dict(name=r[1],expected=r[2],analyzed=r[3],excluded=r[4],stats=json.loads(r[5])) for r in db.execute('SELECT * FROM municipalities')}
 last=('','','');processed=0;changed=0;quarantined=collections.Counter();start=time.monotonic()
 while True:
  take=min(batch,limit-processed) if limit else batch
  if take<=0:break
  headers=src.execute("SELECT municipality,zone,section,aux FROM sections WHERE status='downloaded' AND (municipality,zone,section)>(?,?,?) ORDER BY municipality,zone,section LIMIT ?",(*last,take)).fetchall()
  if not headers:break
  first=tuple(headers[0][k] for k in ['municipality','zone','section']);last=tuple(headers[-1][k] for k in ['municipality','zone','section'])
  logs=src.execute('SELECT municipality,zone,section,publication_hash,name,publication_status,sha256 FROM logs WHERE (municipality,zone,section)>=(?,?,?) AND (municipality,zone,section)<=(?,?,?)',(*first,*last)).fetchall()
  bykey=collections.defaultdict(list)
  for row in logs:bykey[tuple(row[k] for k in ['municipality','zone','section'])].append(dict(row))
  old={tuple(row[:3]):row for row in db.execute('SELECT municipality,zone,section,fingerprint,status,reason,stats,metrics FROM analyzed WHERE (municipality,zone,section)>=(?,?,?) AND (municipality,zone,section)<=(?,?,?)',(*first,*last))}
  changes=[];affected=set()
  # Finish every source SELECT before parsing, avoiding a long WAL read snapshot.
  for header in headers:
   key=tuple(header[k] for k in ['municipality','zone','section']);prior=old.get(key)
   try:
    selected=choose_log(header['aux'],bykey[key]);fingerprint=selected['sha256']
    if prior and prior[3]==fingerprint:
     oldmetrics=json.loads(prior[7])
     needs_context=oldmetrics.get('archivedPackages',0)>0 and oldmetrics.get('sectionContextVersion')!=1
     if not needs_context and not (prior[4]=='excluded' and prior[5]=='machine_clock_discontinuity'):continue
    body=src.execute('SELECT body FROM logs WHERE municipality=? AND zone=? AND section=? AND publication_hash=? AND name=?',(*key,selected['publication_hash'],selected['name'])).fetchone()['body']
    changes.append((key,prior,fingerprint,body,None,selected.get('receivedStatusWarning',False)))
   except Exception as e:
    reason=str(e) if isinstance(e,ValueError) else type(e).__name__;fingerprint='excluded:'+hashlib.sha256(bytes(header['aux'] or b'')).hexdigest()
    if prior and prior[3]==fingerprint:continue
    changes.append((key,prior,fingerprint,None,reason,False))
  for key,prior,fingerprint,body,reason,received_warning in changes:
   if reason:status,stats,metrics='excluded',zero(),{}
   else:
    try:status,reason,stats,metrics=parse_log(body,uf,key[0],key[1],key[2])
    except Exception as e:status,reason,stats,metrics='excluded',type(e).__name__,zero(),{}
   if body is not None:metrics['sectionContextVersion']=1
   if received_warning:metrics['receivedPublicationStatus']=1
   m=municipal[key[0]]
   if prior:
    if prior[4]=='analyzed':m['analyzed']-=1;add(m['stats'],json.loads(prior[6]),-1)
    else:m['excluded']-=1
   if status=='analyzed':m['analyzed']+=1;add(m['stats'],stats)
   else:m['excluded']+=1;quarantined[reason]+=1
   db.execute('INSERT OR REPLACE INTO analyzed VALUES(?,?,?,?,?,?,?,?)',(*key,fingerprint,status,reason,json.dumps(stats,separators=(',',':')),json.dumps(metrics,separators=(',',':'))));affected.add(key[0]);changed+=1
  for code in affected:
   m=municipal[code];db.execute('UPDATE municipalities SET analyzed=?,excluded=?,stats=? WHERE code=?',(m['analyzed'],m['excluded'],json.dumps(m['stats'],separators=(',',':')),code))
  db.commit();processed+=len(headers)
 download_status=dict(src.execute('SELECT status,count(*) FROM sections GROUP BY status').fetchall())
 db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('retrieval',json.dumps(download_status)));db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('updatedAt',dt.datetime.now(dt.timezone.utc).isoformat()));db.commit();src.close();db.close()
 return dict(uf=uf,scanned=processed,changed=changed,newExclusions=dict(quarantined),seconds=round(time.monotonic()-start,2))

def geography_names():
 p=PUBLIC/'geography.json'
 if not p.exists():return {}
 try:
  g=json.loads(p.read_text());rows=g.get('municipalities',g)
  if isinstance(rows,list):return {str(r.get('id',str(r.get('uf','')).lower()+':'+str(r.get('tseCode',r.get('code',''))))):r for r in rows}
  return rows if isinstance(rows,dict) else {}
 except Exception:return {}

def export_summary():
 municipal=[];states=[];regions={};all_totals=zero();domestic_totals=zero();all_cov=collections.Counter();dom_cov=collections.Counter();quarantines=collections.Counter();geo=geography_names()
 for uf,(name,region) in UF_INFO.items():
  src=sqlite3.connect(f'file:{RAW/(uf+".sqlite")}?mode=ro',uri=True);retrieval=dict(src.execute('SELECT status,count(*) FROM sections GROUP BY status'));src.close()
  state=dict(uf=uf.upper(),id=uf,name=name,region=region,isOverseas=uf=='zz',expectedSections=sum(v for k,v in retrieval.items() if k!='aggregated'),downloadedSections=retrieval.get('downloaded',0),analyzedSections=0,excludedSections=0,**zero())
  path=ANALYSIS/(uf+'.sqlite')
  if path.exists():
   c=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
   for code,mname,expected,analyzed,excluded,statsjson in c.execute('SELECT * FROM municipalities ORDER BY code'):
    stats=json.loads(statsjson);key=uf+':'+code;georow=geo.get(key,{})
    if not isinstance(georow,dict):georow={}
    row=dict(id=key,uf=uf.upper(),code=code,name=georow.get('name',mname),region=region,isOverseas=uf=='zz',expectedSections=expected,analyzedSections=analyzed,excludedSections=excluded,**stats)
    municipal.append(row);state['analyzedSections']+=analyzed;state['excludedSections']+=excluded;add(state,stats)
   quarantines.update({reason:n for reason,n in c.execute("SELECT reason,count(*) FROM analyzed WHERE status='excluded' GROUP BY reason")});c.close()
  if path.exists():
   c=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
   state['receivedPublicationStatusSections']=c.execute("SELECT count(*) FROM analyzed WHERE status='analyzed' AND json_extract(metrics,'$.receivedPublicationStatus')=1").fetchone()[0]
   state['clockUnclassifiedSections']=c.execute("SELECT count(*) FROM analyzed WHERE status='analyzed' AND json_extract(metrics,'$.timingUnavailableReason')='machine_clock_discontinuity'").fetchone()[0]
   c.close()
  state['missingSections']=max(0,state['expectedSections']-state['downloadedSections']);states.append(state)
  if region not in regions:regions[region]=dict(id=region,name=REGIONS[region],isOverseas=uf=='zz',expectedSections=0,downloadedSections=0,analyzedSections=0,excludedSections=0,missingSections=0,**zero())
  rg=regions[region]
  for field in ['expectedSections','downloadedSections','analyzedSections','excludedSections','missingSections']:rg[field]+=state[field]
  add(rg,state);add(all_totals,state)
  cov=dict(expectedSections=state['expectedSections'],downloadedSections=state['downloadedSections'],analyzedSections=state['analyzedSections'],excludedSections=state['excludedSections'],missingSections=state['missingSections'],retrievalErrors=retrieval.get('error',0),pendingSections=retrieval.get('pending',0),noLogSections=retrieval.get('no_log',0))
  all_cov.update(cov)
  if uf!='zz':add(domestic_totals,state);dom_cov.update(cov)
 domestic_cov=dict(dom_cov);domestic_cov['analyzedPercent']=round(100*dom_cov['analyzedSections']/dom_cov['expectedSections'],4) if dom_cov['expectedSections'] else 0
 domestic_cov['downloadedPercent']=round(100*dom_cov['downloadedSections']/dom_cov['expectedSections'],4) if dom_cov['expectedSections'] else 0
 domestic_cov['downloadedNotYetAnalyzedSections']=max(0,dom_cov['downloadedSections']-dom_cov['analyzedSections']-dom_cov['excludedSections'])
 complete=dom_cov['analyzedSections']==dom_cov['expectedSections'] and not dom_cov['excludedSections']
 summary=dict(analysisVersion='3-section-bound-archives',publicationStatusPolicy='Prefer latest uniquely published Totalizado log. If no final version exists, a single unique published Recebido log is included provisionally, with a source-status warning and internal event reconciliation; this is not a claim of signature verification. Clock-discontinuous sections retain reconciled ballot totals in the unclassified time bucket.',receivedPublicationStatusSections=sum(s.get('receivedPublicationStatusSections',0) for s in states),clockUnclassifiedSections=sum(s.get('clockUnclassifiedSections',0) for s in states),updatedAt=dt.datetime.now(dt.timezone.utc).isoformat(),scopeLabel='Brazil domestic polling sections',status='complete' if complete else 'partial',date='2026-10-25',intervalMinutes=5,coverage=domestic_cov,allCoverage=dict(all_cov),totals=domestic_totals,allTotals=all_totals,histogram=domestic_totals['histogram'],regions=list(regions.values()),states=states,municipalities=municipal,quarantineReasons=dict(quarantines),methodology=dict(metric='Presidential ballot confirmation and automatic-null records',timestampBasis='Voting-machine clock, normalized to Brasília time for domestic sections',events=['Voto confirmado para [Presidente]','Atribuido voto nulo por suspensão [Presidente]'],reconciliation='Presidential event count must equal completed voter-session count within each included section; this is internal log reconciliation, not yet a comparison against every official result bulletin.',bucketDefinitions=dict(before17='t < 17:00:00 Brasília',between17and19='17:00:00 <= t < 19:00:00 Brasília',after19='t >= 19:00:00 Brasília',unclassified='Overseas clock offsets not inferred, or timestamp cannot be normalized'),limitations=['This is a partial observed dataset until coverage reaches 100%; missing and quarantined sections are not estimated.','A vote recorded after 17:00 is not proof it depended on the extension: voters already in line could legally vote after 17:00.','Machine-clock timestamps may differ from the exact instant a person cast a ballot.','No candidate choice is inferred from timestamped logs. Blank and null ballots are included.','Overseas records are retained separately and are unclassified in the Brasília cutoff buckets.','Downloaded ZIP CRC checks and SHA-256 fingerprints are used; the TSE digital signature envelope has not been independently verified.'],sources=SOURCES))
 for totals in [domestic_totals,all_totals,*states,*regions.values(),*municipal]:
  assert totals['total']==sum(totals[k] for k in ['before17','between17and19','after19','unclassified'])
  assert totals['total']==sum(totals['histogram'])+totals['unclassified']
 for field in METRIC_FIELDS:
  assert domestic_totals[field]==sum(m[field] for m in municipal if not m['isOverseas'])
  assert all_totals[field]==sum(m[field] for m in municipal)
 PUBLIC.mkdir(parents=True,exist_ok=True);temp=PUBLIC/'summary.partial';temp.write_text(json.dumps(summary,ensure_ascii=False,separators=(',',':')));temp.replace(PUBLIC/'summary.json')
 print(json.dumps(dict(exported=True,status=summary['status'],total=domestic_totals['total'],coverage=domestic_cov,quarantineReasons=dict(quarantines))),flush=True)
 return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--limit-per-state',type=int);ap.add_argument('--workers',type=int,default=8);ap.add_argument('--batch-size',type=int,default=64);ap.add_argument('--watch',action='store_true');ap.add_argument('--interval',type=int,default=30);ap.add_argument('--export-only',action='store_true');args=ap.parse_args()
 if args.export_only:export_summary();return
 while True:
  tasks=[(uf,args.limit_per_state,args.batch_size) for uf in UF_INFO]
  with cf.ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
   futures=[pool.submit(analyze_state,task) for task in tasks]
   last_export=time.monotonic()
   for future in cf.as_completed(futures):
    result=future.result();print(json.dumps(result),flush=True)
    if time.monotonic()-last_export>=10:
     export_summary();last_export=time.monotonic()
  export_summary()
  if not args.watch:break
  time.sleep(args.interval)
if __name__=='__main__':main()
