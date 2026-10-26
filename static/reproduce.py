#!/usr/bin/env python3
"""Reproduce the Brazil 2026 presidential runoff timing analysis with Python 3.9+.

No packages, proxies, threads, asynchronous I/O or cloud account are required.
Exactly one HTTP connection is active at a time and reused when TSE permits.

Offline check:
    python3 reproduce.py --self-test
Small diagnostic run (PARTIAL results):
    python3 reproduce.py --database smoke.sqlite --output smoke.json --limit 10
Full run / resume the same run:
    python3 reproduce.py --database brazil-2026.sqlite --output results.json
Optional additional public files:
    python3 reproduce.py --database brazil-2026.sqlite --output results.json --all-files

A full sequential run involves many requests and substantial data: the original
log corpus alone is roughly 90 GB; bulletins and optional additional files add
storage. Do not rely on a fixed ETA: network latency, TSE cooldowns and retries
can make a one-connection run take days. Keep the computer awake and connected.
Ctrl+C preserves committed work and writes a partial results snapshot.
Run the identical command again to skip completed sections and retry failures.

Default downloads: all original log archives and bulletins needed for this
analysis, plus official result JSONs/indices/aux metadata. --all-files additionally
fetches RDV, signature envelopes and bulletin images for the selected publication.
Raw inputs are stored inside SQLite, never as 500,000 individual files. SQLite may
briefly create its normal rollback journal next to the database.

Time bins are [before 17:00), [17:00,19:00), and [19:00,onward), in Brasília time.
Do not interpret after 17:00 as a causal count of votes enabled by the extension.
Overseas, clock-discontinuous and bulletin-only surplus counts are untimed.
A mismatching section retains its official bulletin total entirely untimed; no
missing log event is assigned an invented timestamp or candidate-by-time label.
Candidate-by-time totals cannot be recovered from these public records; output
contains official overall candidate totals and explicitly null candidate-by-time.
"""
from __future__ import annotations
import argparse,collections,datetime as dt,email.utils,hashlib,http.client,io,json,os,shutil,sqlite3,ssl,time,urllib.parse,zipfile,zlib
from pathlib import Path
VERSION='2026-10-26-single-connection-v3-bulletin-untimed-gaps'
ORIGIN='https://resultados.tse.jus.br'
DOCS=[
 'https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/log-do-ecossistema',
 'https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/formato-arquivos-de-bu-rdv-e-assinatura-digital/@@download/file',
 'https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea18-arquivo-auxiliar-de-secao',
 'https://www.tse.jus.br/eleicoes/eleicoes-2026-content/arquivos/divulgacao-de-resultados/tse-ea20-arquivo-de-resultado-unificado',
 'https://www.tse.jus.br/comunicacao/noticias/2026/Setembro/faltam-26-dias-votacao-comeca-e-termina-no-mesmo-horario-em-todo-o-pais',
 'https://www.tre-am.jus.br/comunicacao/noticias/2026/Setembro/eleitorado-do-amazonas-deve-ficar-atento-aos-horarios-de-votacao-nas-eleicoes-2026'
]
DATE='25/10/2026'
BINS=288
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
     if target and not all(context.values()) and line.startswith(DATE) and len(parts)>=5 and parts[3]=='VOTA':metrics['unknownSectionContextRows']+=1
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

class FetchError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class SequentialHTTP:
    """One synchronous HTTPS connection; a reconnect never overlaps another."""
    def __init__(self):
        self.connection = None
        self.last_request = 0.0
        self.requests = 0

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def get(self, url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'resultados.tse.jus.br':
            raise ValueError('Only the documented TSE HTTPS origin is permitted')
        for attempt in range(8):
            time.sleep(max(0, 0.03 - (time.monotonic() - self.last_request)))
            try:
                if self.connection is None:
                    self.connection = http.client.HTTPSConnection(parsed.netloc, timeout=60, context=ssl.create_default_context())
                self.last_request = time.monotonic()
                self.requests += 1
                self.connection.request('GET', urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, '')),
                                        headers={'User-Agent': 'Brazil2026Reproduction/1.0', 'Accept-Encoding': 'identity'})
                response = self.connection.getresponse()
                body = response.read()
                status = response.status
                retry_after = response.getheader('Retry-After')
                if status == 200:
                    return body
                if status == 404:
                    raise FetchError(404, 'TSE file not available: ' + url)
                if status not in (429, 500, 502, 503, 504):
                    raise FetchError(status, 'HTTP %s: %s' % (status, url))
                # TSE documents a ten-minute IP cooldown. Do not keep resetting it.
                wait = 600 if status == 429 else min(60, 2 ** attempt)
                if retry_after:
                    try:
                        wait = max(wait, int(retry_after))
                    except ValueError:
                        try:
                            retry_time = email.utils.parsedate_to_datetime(retry_after)
                            wait = max(wait, (retry_time - dt.datetime.now(dt.timezone.utc)).total_seconds())
                        except (TypeError, ValueError):
                            pass
                print('HTTP %s; waiting %.0f seconds before retry' % (status, wait), flush=True)
                self.close()
                time.sleep(max(0, wait))
            except FetchError:
                raise
            except (OSError, http.client.HTTPException):
                self.close()
                if attempt == 7:
                    raise
                time.sleep(min(60, 2 ** attempt))
        raise FetchError(503, 'Retry budget exhausted: ' + url)


def connect_database(path):
    Path(path).resolve().parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=DELETE')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS resources(url TEXT PRIMARY KEY, kind TEXT, body BLOB, sha256 TEXT, fetched_at TEXT);
    CREATE TABLE IF NOT EXISTS sections(uf TEXT, municipality TEXT, zone TEXT, section TEXT, name TEXT,
        region TEXT, metadata TEXT, status TEXT DEFAULT 'pending', reason TEXT, stats TEXT,
        metrics TEXT, warning TEXT, all_files_saved INTEGER DEFAULT 0, PRIMARY KEY(uf,municipality,zone,section));
    CREATE TABLE IF NOT EXISTS raw_files(uf TEXT, municipality TEXT, zone TEXT, section TEXT,
        publication_hash TEXT, name TEXT, file_type TEXT, source_url TEXT, sha256 TEXT,
        byte_count INTEGER, encoding TEXT, body BLOB, fetched_at TEXT,
        PRIMARY KEY(uf,municipality,zone,section,publication_hash,name));
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
    ''')
    if 'all_files_saved' not in {r[1] for r in db.execute('PRAGMA table_info(sections)')}:
        db.execute('ALTER TABLE sections ADD COLUMN all_files_saved INTEGER DEFAULT 0')
    db.commit()
    return db


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def resource(db, http, url, kind, refresh=False):
    old = db.execute('SELECT body,sha256 FROM resources WHERE url=?', (url,)).fetchone()
    if old and not refresh:
        if hashlib.sha256(old[0]).hexdigest() != old[1]:
            raise ValueError('Cached resource checksum mismatch: ' + url)
        return old[0]
    body = http.get(url)
    db.execute('INSERT OR REPLACE INTO resources VALUES(?,?,?,?,?)',
               (url, kind, body, hashlib.sha256(body).hexdigest(), now()))
    db.commit()
    return body


def raw_file(db, http, key, publication_hash, info, base):
    name = info['nm']
    if '/' in name or '\\' in name or not name.startswith('o03221'):
        raise ValueError('Unexpected raw filename')
    parameters = (*key, publication_hash, name)
    saved = db.execute('SELECT body,encoding,sha256 FROM raw_files WHERE uf=? AND municipality=? AND zone=? AND section=? AND publication_hash=? AND name=?', parameters).fetchone()
    if saved:
        body = zlib.decompress(saved[0]) if saved[1] == 'zlib' else saved[0]
        if hashlib.sha256(body).hexdigest() != saved[2]:
            raise ValueError('Cached raw-file checksum mismatch')
        return body
    url = base + '/' + publication_hash + '/' + name
    body = http.get(url)
    if name.endswith(('.jez', '.zip')):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if archive.testzip():
                raise ValueError('ZIP CRC check failed')
    # Original bytes are recoverable exactly; filenames and SHA-256 are retained.
    encoding = 'identity' if body.startswith(b'PK') else 'zlib'
    stored = body if encoding == 'identity' else zlib.compress(body, level=1)
    db.execute('INSERT OR REPLACE INTO raw_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (*parameters, info.get('tp', ''), url, hashlib.sha256(body).hexdigest(), len(body), encoding, stored, now()))
    db.commit()
    return body


# Minimal BER reader for the official 2026 BU ASN.1 layout. This decodes the
# bulletin but does NOT verify its digital signature. No third-party package.
class BERNode:
    def __init__(self, cls, tag, constructed, value, children):
        self.cls, self.tag, self.constructed = cls, tag, constructed
        self.value, self.children = value, children


def read_ber(data, position=0, depth=0):
    if depth > 40 or position >= len(data):
        raise ValueError('Invalid BER nesting')
    first = data[position]
    position += 1
    cls, constructed, tag = first >> 6, bool(first & 32), first & 31
    if tag == 31:
        tag = 0
        while True:
            b = data[position]
            position += 1
            tag = (tag << 7) | (b & 127)
            if not b & 128:
                break
    length_byte = data[position]
    position += 1
    indefinite = length_byte == 128
    if indefinite:
        if not constructed:
            raise ValueError('Indefinite primitive BER value')
        end = None
    elif length_byte & 128:
        size = length_byte & 127
        if not size or size > 8:
            raise ValueError('Invalid BER length')
        length = int.from_bytes(data[position:position + size], 'big')
        position += size
        end = position + length
    else:
        end = position + length_byte
    start = position
    children = []
    if constructed:
        while (end is None and data[position:position + 2] != b'\x00\x00') or (end is not None and position < end):
            child, position = read_ber(data, position, depth + 1)
            children.append(child)
        if end is None:
            end = position
            position += 2
        elif position != end:
            raise ValueError('BER child exceeds parent')
    else:
        if end > len(data):
            raise ValueError('Truncated BER value')
        position = end
    return BERNode(cls, tag, constructed, data[start:end], children), position


def integer(node):
    if node.constructed:
        raise ValueError('Expected primitive ASN.1 integer')
    return int.from_bytes(node.value, 'big', signed=True)


def bulletin_counts(body, key):
    envelope, _ = read_ber(body)
    contents = [n for n in envelope.children if n.cls == 0 and n.tag == 4]
    if not contents:
        raise ValueError('BU envelope has no content')
    bu, _ = read_ber(contents[-1].value)
    fields = bu.children
    if len(fields) < 9 or integer(fields[1]) != 2:
        raise ValueError('BU is not an official 2026 bulletin')
    identification = fields[3].children
    municipality, zone = identification[0].children
    actual = (integer(municipality), integer(zone), integer(identification[2]))
    if actual != tuple(int(n) for n in key[1:]):
        raise ValueError('BU section identity does not match source section')
    position = 8 if fields[7].cls == 2 and fields[7].tag == 1 else 7
    categories = collections.Counter()
    candidate_votes = collections.Counter()
    found = False
    turnout = None
    for election in fields[position].children:
        values = election.children
        if integer(values[0]) != 6258:
            continue
        for result in values[4].children:
            result_fields = result.children
            for office in result_fields[2].children:
                office_fields = office.children
                code = office_fields[0]
                if code.cls != 2 or code.tag != 1 or integer(code) != 1:
                    continue
                found = True
                turnout = integer(result_fields[1])
                for vote in office_fields[2].children:
                    vote_fields = vote.children
                    kind, count = integer(vote_fields[0]), integer(vote_fields[1])
                    categories[str(kind)] += count
                    if kind == 1:
                        identification = next((n for n in vote_fields if n.cls == 2 and n.tag == 3), None)
                        if identification is None:
                            raise ValueError('Nominal ballot missing candidate identifier')
                        candidate_votes[str(integer(identification.children[1]))] += count
    if not found:
        raise ValueError('BU lacks presidential election 6258 results')
    total = sum(categories.values())
    if total != turnout:
        raise ValueError('BU ballot categories differ from presidential turnout')
    return dict(total=total, turnout=turnout, categories=dict(categories), candidateVotes=dict(candidate_votes))


def select_publication(aux):
    if str(aux.get('st', '')).lower() in ('anulada', 'não apurada', 'não instalada'):
        raise ValueError('section_' + str(aux['st']))
    hashes = aux.get('hashes', [])
    totalized = [h for h in hashes if h.get('st') == 'Totalizado']
    if len(totalized) == 1:
        return totalized[0], None
    if len(totalized) > 1:
        raise ValueError('ambiguous_totalized_publications')
    received = [h for h in hashes if h.get('st') == 'Recebido']
    if len(hashes) == 1 and len(received) == 1:
        return received[0], 'Unique published Received hash; bulletin totals retained provisionally, timing validated separately; aux does not identify it as Totalizado.'
    raise ValueError('no_unambiguous_eligible_publication')


def prepare_sources(db, http, refresh):
    base = ORIGIN + '/oficial/ele2026/6258/dados'
    official = {}
    urls = [('br', base + '/br/br-c0001-e006258-u.json')]
    urls += [(uf, base + '/' + uf + '/' + uf + '-c0001-e006258-u.json') for uf in UF_INFO]
    for uf, url in urls:
        obj = json.loads(resource(db, http, url, 'official_ea20', refresh))
        if obj.get('ele') != '6258' or obj.get('t') != '2' or obj.get('f') != 'o':
            raise ValueError('Unexpected official result identity')
        official[uf] = obj
    city_turnout = {}
    for uf in UF_INFO:
        url = base + '/' + uf + '/' + uf + '-e006258-ab.json'
        obj = json.loads(resource(db, http, url, 'official_ea15', refresh))
        for entry in obj['abr']:
            if entry.get('tpabr') in ('mu', 'mun'):
                city_turnout[uf + ':' + entry['cdabr']] = int(entry['e']['c'])
        url = ORIGIN + '/oficial/ele2026/arquivo-urna/3221/config/' + uf + '/' + uf + '-p003221-cs.json'
        index = json.loads(resource(db, http, url, 'section_index', refresh))
        if str(index.get('cdp')) != '3221':
            raise ValueError('Unexpected section-index pleito')
        batch = []
        for area in index['abr']:
            for city in area['mu']:
                for zone in city['zon']:
                    for section in zone['sec']:
                        status = 'aggregated' if 'nsp' in section else 'pending'
                        batch.append((uf, str(city['cd']).zfill(5), str(zone['cd']).zfill(4), str(section['ns']).zfill(4), city['nm'], UF_INFO[uf][1], json.dumps(section), status))
        db.executemany('INSERT INTO sections(uf,municipality,zone,section,name,region,metadata,status) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(uf,municipality,zone,section) DO UPDATE SET name=excluded.name,region=excluded.region,metadata=excluded.metadata', batch)
        db.commit()
    return official, city_turnout


def official_totals(obj):
    return dict(total=int(obj['v']['tv']), valid=int(obj['v']['vv']), blank=int(obj['v']['vb']),
                null=int(obj['v']['tvn']), turnout=int(obj['e']['c']),
                totalizedSections=int(obj['s']['st']), installedSections=int(obj['s']['si']),
                notInstalledSections=int(obj['s']['sni']), final=obj.get('tf') == 's')


def official_candidates(obj):
    result = []
    for office in obj.get('carg', []):
        if office.get('cd') != '1':
            continue
        for group in office.get('agr', []):
            for party in group.get('par', []):
                for candidate in party.get('cand', []):
                    result.append(dict(number=candidate['n'], name=candidate['nmu'], votes=int(candidate['vap'])))
    return sorted(result, key=lambda r: r['number'])


def reconcile_section_timing(status, reason, stats, metrics, bu):
    """Preserve observed timing; use official BU-only differences as UNTImed.

    A BU surplus does not tell us when the unmatched ballots were cast. A log
    mismatch or a BU deficit invalidates all timing for that section, not its
    independently decoded bulletin total.
    """
    metrics = dict(metrics)
    presidential_events = metrics.get('presidentialConfirmations', 0) + metrics.get('automaticNullBallots', 0)
    metrics['rawPresidentialEvents'] = presidential_events
    metrics['trustedObservedLogRecords'] = 0
    metrics['bulletinMinusRawPresidentialEvents'] = bu['total'] - presidential_events
    metrics['bulletin'] = bu
    warnings = []
    if status != 'analyzed':
        output = zero()
        output.update(total=bu['total'], unclassified=bu['total'])
        metrics['untimedBulletinFallbackReason'] = reason or 'timing_log_not_available'
        warnings.append('Entire section is untimed: log/session timing could not be validated; authoritative BU total retained.')
    elif bu['total'] < stats['total']:
        output = zero()
        output.update(total=bu['total'], unclassified=bu['total'])
        metrics['untimedBulletinFallbackReason'] = 'observed_log_count_exceeds_bulletin'
        metrics['timingQuarantined'] = True
        warnings.append('Entire section timing quarantined: log count exceeds authoritative BU total; BU total retained untimed.')
    else:
        output = dict(stats)
        output['histogram'] = list(stats['histogram'])
        metrics['trustedObservedLogRecords'] = stats['total']
        difference = bu['total'] - stats['total']
        if difference:
            output['total'] += difference
            output['unclassified'] += difference
            metrics['untimedBulletinSurplus'] = difference
            warnings.append('Official BU contains additional ballots without matching validated log events; surplus retained untimed.')
    assert output['total'] == bu['total']
    assert output['total'] == sum(output[k] for k in ['before17','between17and19','after19','unclassified'])
    assert output['total'] == sum(output['histogram']) + output['unclassified']
    return output, metrics, warnings


def process_section(db, http, row, all_files, refresh):
    uf, municipality, zone, section = key = tuple(row[:4])
    base = ORIGIN + '/oficial/ele2026/arquivo-urna/3221/dados/' + '/'.join(key)
    url = base + '/p003221-' + uf + '-m' + municipality + '-z' + zone + '-s' + section + '-aux.json'
    warning = None
    try:
        aux = json.loads(resource(db, http, url, 'section_aux', refresh))
        selected, warning = select_publication(aux)
        files = selected.get('arq', [])
        bodies = {}
        for info in files:
            if all_files or info.get('tp') in ('log', 'logsa', 'bu'):
                bodies[info['tp']] = raw_file(db, http, key, selected['hash'], info, base)
        if 'bu' not in bodies:
            raise ValueError('required_bulletin_unavailable')
        bu = bulletin_counts(bodies['bu'], key)
        if 'log' in bodies:
            parsed_status, parsed_reason, stats, metrics = parse_log(bodies['log'], uf, municipality, zone, section)
        else:
            parsed_status, parsed_reason, stats, metrics = 'excluded', 'no_vota_timing_log_available', zero(), {}
        stats, metrics, timing_warnings = reconcile_section_timing(parsed_status, parsed_reason, stats, metrics, bu)
        if timing_warnings:
            warning = ((warning + ' ') if warning else '') + ' '.join(timing_warnings)
        metrics['publicationHash'] = selected['hash']
        metrics['publicationStatus'] = selected['st']
        metrics['receivedPublicationStatus'] = int(selected['st'] == 'Recebido')
        if metrics.get('unknownSectionContextRows'):
            warning = (warning + ' ' if warning else '') + 'Some log rows have no complete section header; BU/session totals still reconcile.'
        status, reason = 'done', None
    except FetchError as error:
        status = 'unavailable' if error.status == 404 else 'error'
        reason, stats, metrics = str(error), None, None
    except (OSError, http.client.HTTPException) as error:
        status, reason, stats, metrics = 'error', type(error).__name__, None, None
    except Exception as error:
        status, reason, stats, metrics = 'quarantined', str(error) if isinstance(error, ValueError) else type(error).__name__, None, None
    db.execute('UPDATE sections SET status=?,reason=?,stats=?,metrics=?,warning=? WHERE uf=? AND municipality=? AND zone=? AND section=?',
               (status, reason, json.dumps(stats, separators=(',', ':')) if stats else None,
                json.dumps(metrics, separators=(',', ':')) if metrics else None, warning, *key))
    if all_files and status == 'done':
        db.execute('UPDATE sections SET all_files_saved=1 WHERE uf=? AND municipality=? AND zone=? AND section=?',key)
    db.commit()
    return status


def output_results(db, official, city_turnout, output, limited):
    municipalities = {}
    status_counts = dict(db.execute('SELECT status,count(*) FROM sections GROUP BY status'))
    warnings = collections.Counter()
    reasons = collections.Counter()
    expected = {}
    for uf, city, name, region, count in db.execute("SELECT uf,municipality,name,region,count(*) FROM sections WHERE status!='aggregated' GROUP BY uf,municipality,name,region"):
        expected[(uf, city)] = dict(id=uf + ':' + city, uf=uf.upper(), code=city, name=name, region=region,
                                    isOverseas=uf == 'zz', expectedSections=count, analyzedSections=0, candidateVotes={}, validVotes=0, blankVotes=0, nullVotes=0, otherVotes=0, **zero())
    observed_candidates = collections.Counter()
    trusted_domestic_log_records=0
    trusted_all_log_records=0
    known_untimed_bulletin_surplus=0
    for uf, city, stats_json, metrics_json, warning in db.execute("SELECT uf,municipality,stats,metrics,warning FROM sections WHERE status='done'"):
        item = expected[(uf, city)]
        stats, metrics = json.loads(stats_json), json.loads(metrics_json)
        trusted_all_log_records+=metrics.get('trustedObservedLogRecords',0)
        if uf!='zz':trusted_domestic_log_records+=metrics.get('trustedObservedLogRecords',0)
        known_untimed_bulletin_surplus+=metrics.get('untimedBulletinSurplus',0)
        if metrics.get('untimedBulletinFallbackReason'):warnings['untimedBulletinFallbackSections']+=1
        if metrics.get('timingQuarantined'):warnings['timingQuarantinedSections']+=1
        add(item, stats)
        item['analyzedSections'] += 1
        if metrics.get('receivedPublicationStatus'):
            warnings['receivedPublicationSections'] += 1
        if metrics.get('unknownSectionContextRows'):
            warnings['unknownArchiveContextSections'] += 1
        if metrics.get('timingUnavailableReason'):
            warnings['clockDiscontinuitySections'] += 1
        bulletin=metrics.get('bulletin',{})
        categories=bulletin.get('categories',{})
        item['validVotes']+=categories.get('1',0)
        item['blankVotes']+=categories.get('2',0)
        item['nullVotes']+=categories.get('3',0)
        item['otherVotes']+=sum(n for kind,n in categories.items() if kind not in ('1','2','3'))
        for candidate, count in bulletin.get('candidateVotes', {}).items():
            observed_candidates[candidate] += count
            item['candidateVotes'][candidate]=item['candidateVotes'].get(candidate,0)+count
    for reason, count in db.execute("SELECT reason,count(*) FROM sections WHERE status IN ('quarantined','error','unavailable') GROUP BY reason"):
        reasons[reason] += count
    domestic, overseas, all_totals = zero(), zero(), zero()
    states = {}; regions = {}
    for item in expected.values():
        uf = item['uf'].lower(); region = item['region']
        if uf not in states:
            states[uf] = dict(uf=uf.upper(), name=UF_INFO[uf][0], region=region, expectedSections=0, analyzedSections=0, candidateVotes={}, validVotes=0, blankVotes=0, nullVotes=0, otherVotes=0, **zero())
        if region not in regions:
            regions[region] = dict(id=region, name=REGIONS[region], expectedSections=0, analyzedSections=0, candidateVotes={}, validVotes=0, blankVotes=0, nullVotes=0, otherVotes=0, **zero())
        for target in (states[uf], regions[region]):
            add(target, item)
            target['expectedSections'] += item['expectedSections'];target['analyzedSections'] += item['analyzedSections']
            for field in ('validVotes','blankVotes','nullVotes','otherVotes'):target[field]+=item[field]
            for candidate,count in item['candidateVotes'].items():target['candidateVotes'][candidate]=target['candidateVotes'].get(candidate,0)+count
        add(all_totals, item)
        add(overseas if item['isOverseas'] else domestic, item)
        item['officialTurnout'] = city_turnout.get(item['id'])
        item['officialTurnoutMinusObserved'] = None if item['officialTurnout'] is None else item['officialTurnout'] - item['total']
        item['allExpectedSectionsAnalyzed'] = item['analyzedSections'] == item['expectedSections']
        item['municipalityReconciled'] = item['allExpectedSectionsAnalyzed'] and item['officialTurnoutMinusObserved'] == 0
    national_official = official_totals(official['br'])
    overseas_official = official_totals(official['zz'])
    domestic_official = {k: national_official[k] - overseas_official[k] for k in ['total', 'valid', 'blank', 'null', 'turnout', 'totalizedSections', 'installedSections', 'notInstalledSections']}
    all_expected = sum(v['expectedSections'] for v in expected.values())
    def official_headline(reference, observed):
        headline=zero()
        headline['total']=reference['total']
        for field in ['before17','between17and19','after19']:
            headline[field]=observed[field]
        headline['histogram']=list(observed['histogram'])
        timed=sum(headline[k] for k in ['before17','between17and19','after19'])
        if timed>headline['total']:
            raise ValueError('Observed timing exceeds official total; reconciliation required, not a negative remainder')
        headline['unclassified']=headline['total']-timed
        return headline
    national_headline=official_headline(national_official,all_totals)
    domestic_headline=official_headline(domestic_official,domestic)

    data = dict(schemaVersion=1, scriptVersion=VERSION, generatedAt=now(), date='2026-10-25', intervalMinutes=5,
                status='complete_observed_cohort' if status_counts.get('done', 0) == all_expected and not limited else 'partial',
                smokeTest=limited, scopeLabel='Domestic timing; overseas retained as unclassified',
                coverage=dict(expectedPrimarySections=all_expected, statuses=status_counts, warnings=dict(warnings), exclusionReasons=dict(reasons)),
                totals=domestic, allTotals=all_totals, overseasTotals=overseas,
                headlineTotals=national_headline, domesticHeadlineTotals=domestic_headline,
                rawObservedTrustedLogRecords=dict(domestic=trusted_domestic_log_records,allScopes=trusted_all_log_records),
                knownUntimedBulletinSurplus=known_untimed_bulletin_surplus,
                states=list(states.values()), regions=list(regions.values()), municipalities=list(expected.values()),
                official=dict(national=national_official, domestic=domestic_official, overseas=overseas_official,
                              nationalCandidateTotals=official_candidates(official['br']),
                              stateResults=[dict(uf=uf.upper(), totals=official_totals(official[uf]), candidates=official_candidates(official[uf])) for uf in UF_INFO]),
                observedBulletinCandidateTotals=dict(observed_candidates),
                observedBulletinCandidateTotalsScope='All included sections, including overseas; these are overall candidate totals, not time-split totals',
                candidateTotalsByTime=None,
                candidateTotalsByTimeExplanation='Timestamped machine logs do not disclose candidate choice. The secret digital vote record is shuffled; candidate totals cannot be assigned to before/after 17:00 without manufacturing data.',
                comparison=dict(allScopesObservedRecords=trusted_all_log_records, allScopesOfficialBallots=national_official['total'], allScopesOfficialMinusObserved=national_official['total']-trusted_all_log_records, domesticObservedRecords=trusted_domestic_log_records, domesticBulletinReconciledCohort=domestic['total'], domesticOfficialBallots=domestic_official['total'],
                                officialMinusObserved=domestic_official['total'] - trusted_domestic_log_records,
                                interpretation='While any section is unprocessed, unavailable or quarantined, this difference is an incomplete-coverage difference, not a proven vote discrepancy.'),
                methodology=dict(metric='Official bulletin totals with validated presidential log timing and explicit untimed bulletin-only fallbacks',
                                 totalScopes='totals/allTotals describe the processed bulletin cohort. headlineTotals/domesticHeadlineTotals use the full official total and retain every remaining unclassified ballot explicitly.',
                                 publicationPolicy='Prefer a unique Totalizado hash. A unique Received hash is provisional and requires an official BU. Valid log/session timing is retained; BU-only surpluses and mismatching sections are untimed. Ambiguous versions are quarantined.',
                                 clockPolicy='Local machine clocks converted to Brasília. Entire clock-discontinuous section totals are retained as unclassified if ballot counts reconcile. Overseas offsets are not guessed.',
                                 bucketDefinitions={'before17':'t < 17:00:00 BRT','between17and19':'17:00:00 <= t < 19:00:00 BRT','after19':'t >= 19:00:00 BRT'},
                                 integrity='Original-byte SHA256 fingerprints and ZIP CRC; decoding a BU is not independent signature verification.',
                                 causality='After17:00 is not equivalent to votes enabled by extension: people already in line could vote after 17:00.',
                                 noLogExceptions='Official results include41 overseas sections not installed. Missing raw archives must be distinguished from those known exceptions.'),
                sourceManifest=dict(databaseTables=['resources','sections','raw_files'], rawFileMode='all_files' if db.execute("SELECT value FROM metadata WHERE key='all_files'").fetchone()[0] == 'true' else 'logs_and_bulletins',
                                    rawOrigin=ORIGIN, phase='oficial', cycle='ele2026', pleito=3221, election=6258, office=1,
                                    rawFileCount=db.execute('SELECT count(*) FROM raw_files').fetchone()[0],
                                    rawOriginalBytes=db.execute('SELECT coalesce(sum(byte_count),0) FROM raw_files').fetchone()[0],
                                    sourceDocumentation=DOCS,
                                    scriptSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    for item in [domestic, overseas, all_totals, national_headline, domestic_headline, *expected.values(), *states.values(), *regions.values()]:
        assert item['total'] == sum(item[k] for k in ['before17','between17and19','after19','unclassified'])
        assert item['total'] == sum(item['histogram']) + item['unclassified']
        if 'validVotes' in item:
            assert item['total']==sum(item[k] for k in ('validVotes','blankVotes','nullVotes','otherVotes'))
            assert sum(item['candidateVotes'].values())==item['validVotes']
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.partial')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(output)
    print('Saved %s: %s domestic ballots in the processed BU cohort; %s analyzed sections; status=%s' % (output, format(domestic['total'], ','), format(status_counts.get('done', 0), ','), data['status']), flush=True)


def self_test():
    def row(clock, message, mac):
        return '25/10/2026 %s\tINFO\t00000001\tVOTA\t%s\t%016X' % (clock, message, mac)
    def package(lines, nested=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('logd.dat', '\n'.join(lines).encode('latin-1'))
            if nested is not None:archive.writestr('older.jez', nested)
        return stream.getvalue()
    lines = []
    for i, clock in enumerate(['16:59:59','17:00:00','18:59:59','19:00:00']):
        lines += [row(clock,'Voto confirmado para [Presidente]',i*2),row(clock,'O voto do eleitor foi computado',i*2+1)]
    state, reason, stats, metrics = parse_log(package(lines), 'sp', '71072')
    assert state == 'analyzed' and [stats[k] for k in ['total','before17','between17and19','after19']] == [4,1,2,1]
    state, reason, stats, metrics = parse_log(package(lines, package(lines)), 'sp', '71072')
    assert stats['total'] == 4 and metrics['duplicateRows'] == 8
    null = [row('16:00:00','Atribuido voto nulo por suspensão [Presidente]',20),row('16:00:01','O voto do eleitor foi computado',21)]
    state, reason, stats, metrics = parse_log(package(null), 'ac', '01007')
    assert stats['between17and19'] == 1 and stats['total'] == 1
    state, reason, stats, metrics = parse_log(package(lines[:-1]), 'sp', '71072')
    assert state == 'excluded'
    state, reason, stats, metrics = parse_log(package(lines), 'zz', '12345')
    assert stats['unclassified'] == 4 and sum(stats['histogram']) == 0
    assert shift_minutes('am','98477') == 120 and shift_minutes('am','02550') == 60 and shift_minutes('pe','30015') == -60
    backwards = [row('16:00:00','Voto confirmado para [Presidente]',40),row('15:00:00','O voto do eleitor foi computado',41)]
    state, reason, stats, metrics = parse_log(package(backwards), 'sp', '71072')
    assert state == 'analyzed' and stats['unclassified'] == 1 and sum(stats['histogram']) == 0
    try:select_publication({'st':'Recebida','hashes':[{'st':'Recebido'},{'st':'Recebido'}]})
    except ValueError:pass
    else:raise AssertionError('Ambiguous publication was accepted')
    def header(message, mac):
        return '24/10/2026 12:00:00\tINFO\t00000001\tGAP\t%s\t%016X' % (message,mac)
    target_headers=[header('Município: 57932',60),header('Zona Eleitoral: 0009',61),header('Seção Eleitoral: 0085',62)]
    foreign_headers=[header('Município: 57932',70),header('Zona Eleitoral: 0009',71),header('Seção Eleitoral: 0112',72)]
    foreign_votes=[row('12:00:00','Voto confirmado para [Presidente]',80),row('12:00:01','O voto do eleitor foi computado',81)]
    state,reason,stats,metrics=parse_log(package(target_headers+lines,package(foreign_headers+foreign_votes)),'es','57932','0009','0085')
    assert stats['total']==4 and metrics['foreignSectionRowsSkipped']==2
    valid_status,valid_reason,valid_stats,valid_metrics=parse_log(package(lines),'sp','71072')
    extended,check,warnings=reconcile_section_timing(valid_status,valid_reason,valid_stats,valid_metrics,{'total':7})
    assert extended['total']==7 and extended['unclassified']==3 and extended['histogram']==valid_stats['histogram']
    assert check['trustedObservedLogRecords']==4 and check['untimedBulletinSurplus']==3 and warnings
    mismatch,check,warnings=reconcile_section_timing('excluded','count_mismatch',zero(),{'completedSessions':4,'presidentialConfirmations':3},{'total':5})
    assert mismatch['total']==mismatch['unclassified']==5 and sum(mismatch['histogram'])==0 and check['trustedObservedLogRecords']==0
    deficit,check,warnings=reconcile_section_timing(valid_status,valid_reason,valid_stats,valid_metrics,{'total':3})
    assert deficit['total']==deficit['unclassified']==3 and sum(deficit['histogram'])==0 and check['timingQuarantined']
    print('PASS: offline cutoffs, recursion, deduplication, null ballots, mismatch handling, time zones, overseas, clock discontinuity, publication ambiguity, foreign archives and untimed BU surpluses/deficits. No network requests made.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', default='brazil-2026.sqlite', help='One resumable SQLite database; raw inputs are BLOBs, not loose files')
    parser.add_argument('--output', default='results.json', help='Aggregate JSON output; no voter identifiers or raw log rows')
    parser.add_argument('--limit', type=int, help='Smoke-test only: process at most N sections this run; output is partial')
    parser.add_argument('--all-files', action='store_true', help='Also download RDV, signatures and BU images for the selected publication; substantially more storage')
    parser.add_argument('--refresh-status', action='store_true', help='Refresh official JSON/aux metadata and reconsider completed sections; retain cached raw bytes')
    parser.add_argument('--self-test', action='store_true', help='Run small in-memory tests; no network and no database needed')
    args = parser.parse_args()
    if args.self_test:self_test();return
    if args.limit is not None and args.limit < 1:parser.error('--limit must be positive')
    db = connect_database(args.database);http = SequentialHTTP();official = {};city_turnout = {}
    previous_version=db.execute("SELECT value FROM metadata WHERE key='script_version'").fetchone()
    if previous_version and previous_version[0]!=VERSION:
        print('Parser version changed: re-analyzing saved raw bytes; existing downloads are retained.',flush=True)
        db.execute("UPDATE sections SET status='pending' WHERE status='done'")
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('script_version',VERSION))
    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('all_files',json.dumps(args.all_files)));db.commit()
    processed = 0;counts = collections.Counter();start = time.monotonic();last_report = start
    try:
        print('One thread, one sequential HTTP connection, no proxies. Loading cached/public manifests...', flush=True)
        official, city_turnout = prepare_sources(db, http, args.refresh_status)
        cursor = ('','','','')
        while True:
            condition = "status!='aggregated'" if args.refresh_status else ("status!='aggregated' AND (status!='done' OR all_files_saved=0)" if args.all_files else "status NOT IN ('aggregated','done')")
            batch = db.execute('SELECT uf,municipality,zone,section FROM sections WHERE ' + condition + ' AND (uf,municipality,zone,section)>(?,?,?,?) ORDER BY uf,municipality,zone,section LIMIT 64',cursor).fetchall()
            if not batch:break
            for row in batch:
                if args.limit is not None and processed >= args.limit:break
                if shutil.disk_usage(Path(args.database).resolve().parent).free < 10*1024**3:
                    raise RuntimeError('Stopped with under 10 GiB free disk space; resume after freeing space')
                status = process_section(db, http, row, args.all_files, args.refresh_status)
                processed += 1;counts[status] += 1
                if processed == 1 or processed % 25 == 0 or time.monotonic() - last_report >= 30:
                    print(json.dumps(dict(processedThisRun=processed,outcomes=dict(counts),httpRequestsThisRun=http.requests,elapsedSeconds=round(time.monotonic()-start,1))),flush=True);last_report=time.monotonic()
            cursor = tuple(batch[-1])
            if args.limit is not None and processed >= args.limit:break
    except KeyboardInterrupt:
        print('\nInterrupted. Committed raw files and completed sections are preserved; run the same command to resume.',flush=True)
    finally:
        http.close()
        db.rollback()
        if official:output_results(db,official,city_turnout,args.output,args.limit is not None)
        db.close()


if __name__ == '__main__':
    main()
