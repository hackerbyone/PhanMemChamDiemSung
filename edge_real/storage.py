"""Durable captures, revisioned results, retry-safe synchronization."""
import base64
import json
import sqlite3
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
import requests
from core import write_image,read_image

LOCAL_TZ=timezone(timedelta(hours=7))
ROUND_COLUMNS={'shooter_code':'TEXT','shooter':'TEXT','unit':'TEXT','shooting_date':'TEXT',
               'lane':'INTEGER','series':'INTEGER','expected_shots':'INTEGER',
               'total_score':'INTEGER','total_holes':'INTEGER','status':'TEXT','notes':'TEXT'}

def search_text(value):
    value=unicodedata.normalize('NFD',value.casefold().replace('đ','d'))
    return ''.join(c for c in value if not unicodedata.combining(c))

def shooting_day(payload):
    if payload.get('shooting_date'):
        return date.fromisoformat(payload['shooting_date']).isoformat()
    stamp=payload.get('before_acquired_at') or payload.get('created_at')
    if not stamp:return ''
    dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    return (dt.astimezone(LOCAL_TZ) if dt.tzinfo else dt).date().isoformat()

def record_fields(payload):
    return {key:shooting_day(payload) if key=='shooting_date' else payload.get(key) for key in ROUND_COLUMNS}


def utc(): return datetime.now(timezone.utc).isoformat()

def atomic_json(path,value):
    path=Path(path);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');tmp.replace(path)

class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.db=self.root/'rounds.db'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,payload TEXT NOT NULL,synced INTEGER DEFAULT 0)')
            c.execute('CREATE TABLE IF NOT EXISTS shooters(code TEXT PRIMARY KEY,name TEXT NOT NULL,unit TEXT NOT NULL,updated_at TEXT NOT NULL)')
            existing={r[1] for r in c.execute('PRAGMA table_info(rounds)')}
            added=False
            for key,kind in ROUND_COLUMNS.items():
                if key not in existing:
                    c.execute(f'ALTER TABLE rounds ADD COLUMN {key} {kind}');added=True
            c.execute('CREATE INDEX IF NOT EXISTS rounds_day_lane ON rounds(shooting_date,lane)')
            c.execute('CREATE INDEX IF NOT EXISTS rounds_shooter ON rounds(shooter_code)')
            if added:
                for ident,raw in c.execute('SELECT id,payload FROM rounds').fetchall():
                    p=json.loads(raw);self._index(c,ident,p)
                    if p.get('shooter_code') and p.get('shooter') and p.get('unit') and not p.get('demo') and p.get('status')!='DEMO':
                        c.execute('INSERT OR IGNORE INTO shooters VALUES(?,?,?,?)',
                                  (p['shooter_code'],p['shooter'],p['unit'],utc()))

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db)
        try:
            with c:yield c
        finally:c.close()

    def _index(self,c,ident,payload):
        fields=record_fields(payload)
        c.execute('UPDATE rounds SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),ident))

    def list_rounds(self,query='',day_from='',day_to='',lane='',status=''):
        try:
            day_from=date.fromisoformat(day_from).isoformat() if day_from else ''
            day_to=date.fromisoformat(day_to).isoformat() if day_to else ''
        except ValueError:raise ValueError('Ngày lọc phải hợp lệ, dạng YYYY-MM-DD')
        if day_from and day_to and day_from>day_to:raise ValueError('Ngày bắt đầu phải trước ngày kết thúc')
        with self.connect() as c:
            c.row_factory=sqlite3.Row
            sql='SELECT id,'+','.join(ROUND_COLUMNS)+',synced FROM rounds WHERE 1=1';args=[]
            if day_from:sql+=' AND shooting_date>=?';args.append(day_from)
            if day_to:sql+=' AND shooting_date<=?';args.append(day_to)
            if lane:sql+=' AND lane=?';args.append(int(lane))
            if status:sql+=' AND status=?';args.append(status)
            rows=[dict(row) for row in c.execute(sql+' ORDER BY shooting_date DESC,rowid DESC',args)]
        query=search_text(query.strip())
        return [r for r in rows if not query or query in search_text(' '.join(str(r.get(k) or '') for k in ('shooter_code','shooter','unit')))]

    def load_round(self,ident):
        p=self.load_payload(ident)
        folder=self.root/str(uuid.UUID(ident))/f"rev_{p.get('revision',1)}"
        if not folder.exists():folder=self.root/ident
        return p,{n:read_image(folder/(n+'.png')) for n in ('before','after')}

    def load_payload(self,ident):
        with self.connect() as c:
            row=c.execute('SELECT payload FROM rounds WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('Không tìm thấy loạt')
        return json.loads(row[0])

    def list_shooters(self,query=''):
        with self.connect() as c:
            c.row_factory=sqlite3.Row
            rows=[dict(r) for r in c.execute('SELECT * FROM shooters ORDER BY code COLLATE NOCASE')]
        query=search_text(query.strip())
        return [r for r in rows if not query or query in search_text(' '.join((r['code'],r['name'],r['unit'])))]

    def save_shooters(self,rows,replace=False):
        clean=[];codes=set()
        for row in rows:
            values=tuple(str(row.get(k) or '').strip() for k in ('code','name','unit'))
            if any(not v or len(v)>200 for v in values):raise ValueError('Mã, họ tên và đơn vị phải có từ 1 đến 200 ký tự')
            if values[0] in codes:raise ValueError('Trùng mã người bắn trong danh sách: '+values[0])
            codes.add(values[0]);clean.append((*values,utc()))
        with self.connect() as c:
            for values in clean:
                old=c.execute('SELECT name,unit FROM shooters WHERE code=?',(values[0],)).fetchone()
                if old and old!=values[1:3] and not replace:raise ValueError('Mã đã có thông tin khác: '+values[0]+'. Dùng Sửa người đang chọn để cập nhật.')
                c.execute('INSERT INTO shooters VALUES(?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,unit=excluded.unit,updated_at=excluded.updated_at',values)
        return len(clean)

    def list_drafts(self):
        rows=[]
        with self.connect() as c:saved={r[0] for r in c.execute('SELECT id FROM rounds')}
        for path in (self.root/'drafts').glob('*/metadata.json'):
            if path.parent.name in saved:continue
            try:
                p=json.loads(path.read_text(encoding='utf8'))
                if p.get('draft_archived') or not (path.parent/'before.png').exists():continue
                rows.append(dict(p,id=path.parent.name,has_after=(path.parent/'after.png').exists()))
            except (ValueError,OSError):continue
        return sorted(rows,key=lambda p:p.get('before_acquired_at',''),reverse=True)

    def load_draft(self,ident):
        folder=self.root/'drafts'/str(uuid.UUID(ident))
        p=json.loads((folder/'metadata.json').read_text(encoding='utf8'))
        return p,{n:read_image(folder/(n+'.png')) if (folder/(n+'.png')).exists() else None for n in ('before','after')}

    def archive_draft(self,ident):
        path=self.root/'drafts'/str(uuid.UUID(ident))/'metadata.json'
        p=json.loads(path.read_text(encoding='utf8'));p['draft_archived']=True;atomic_json(path,p)
    def capture(self,ident,kind,image,meta):
        folder=self.root/'drafts'/ident;folder.mkdir(parents=True,exist_ok=True)
        # Each capture persists immediately even before inference succeeds.
        write_image(folder/(kind+'.png'),image);atomic_json(folder/'metadata.json',meta)
        return folder
    def save(self,result,meta,before,after,annotated,ident=None,expected_revision=None):
        ident=ident or str(uuid.uuid4());folder=self.root/ident;folder.mkdir(exist_ok=True)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM rounds WHERE id=?',(ident,)).fetchone()
            previous=json.loads(row[0]) if row else None
            if expected_revision is not None and (previous is None or previous.get('revision',1)!=expected_revision):
                raise ValueError('Loạt đã được sửa ở cửa sổ khác. Mở lại lịch sử trước khi sửa tiếp.')
            revision=(previous.get('revision',1)+1) if previous else 1
            payload=dict(result,**meta,id=ident,revision=revision,updated_at=utc(),created_at=previous['created_at'] if previous else utc())
            payload['total_score']=sum(h['score'] for h in payload['detections'] if h['selected'])
            payload['total_holes']=sum(bool(h['selected']) for h in payload['detections'])
            revdir=folder/f'rev_{revision}';revdir.mkdir(exist_ok=True)
            for name,im in [('before',before),('after',after),('annotated',annotated)]:write_image(revdir/(name+'.png'),im)
            atomic_json(revdir/'result.json',payload)
            c.execute('INSERT INTO rounds(id,payload,synced) VALUES(?,?,0) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,synced=0',(ident,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
            self._index(c,ident,payload)
        atomic_json(folder/'result.json',payload)
        return folder,payload
    def sync(self,url,password):
        url=url.rstrip('/');s=requests.Session();r=s.post(url+'/api/manager/login',json={'password':password},timeout=15);r.raise_for_status()
        token=r.json()['token'];count=0
        try:
            with self.connect() as c:
                for ident,raw in c.execute('SELECT id,payload FROM rounds WHERE synced=0 ORDER BY rowid').fetchall():
                    p=json.loads(raw);rev=p.get('revision',1);folder=self.root/ident/f'rev_{rev}'
                    if not folder.exists():folder=self.root/ident
                    images={n:base64.b64encode((folder/(n+'.png')).read_bytes()).decode() for n in ['before','after','annotated']}
                    r=s.post(url+'/api/real-rounds',json={'result':p,'images':images},headers={'X-Manager-Token':token},timeout=120);r.raise_for_status()
                    if r.json().get('id')!=ident:raise ValueError('Server trả sai mã loạt')
                    # A correction saved while network was in flight must remain pending.
                    c.execute('UPDATE rounds SET synced=1 WHERE id=? AND payload=?',(ident,raw));c.commit();count+=1
        finally:
            try:s.post(url+'/api/manager/logout',headers={'X-Manager-Token':token},timeout=5)
            except requests.RequestException:pass
            s.close()
        return count
