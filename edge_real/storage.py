"""Durable captures, revisioned results, retry-safe synchronization."""
import base64
import json
import sqlite3
import uuid
from datetime import datetime,timezone
from pathlib import Path
import requests
from core import write_image


def utc(): return datetime.now(timezone.utc).isoformat()

def atomic_json(path,value):
    path=Path(path);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');tmp.replace(path)

class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.db=self.root/'rounds.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,payload TEXT NOT NULL,synced INTEGER DEFAULT 0)')
    def capture(self,ident,kind,image,meta):
        folder=self.root/'drafts'/ident;folder.mkdir(parents=True,exist_ok=True)
        # Each capture persists immediately even before inference succeeds.
        write_image(folder/(kind+'.png'),image);atomic_json(folder/'metadata.json',meta)
        return folder
    def save(self,result,meta,before,after,annotated,ident=None):
        ident=ident or str(uuid.uuid4());folder=self.root/ident;folder.mkdir(exist_ok=True)
        with sqlite3.connect(self.db) as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM rounds WHERE id=?',(ident,)).fetchone()
            previous=json.loads(row[0]) if row else None
            revision=(previous.get('revision',1)+1) if previous else 1
            payload=dict(result,**meta,id=ident,revision=revision,updated_at=utc(),created_at=previous['created_at'] if previous else utc())
            payload['total_score']=sum(h['score'] for h in payload['detections'] if h['selected'])
            payload['total_holes']=sum(bool(h['selected']) for h in payload['detections'])
            revdir=folder/f'rev_{revision}';revdir.mkdir(exist_ok=True)
            for name,im in [('before',before),('after',after),('annotated',annotated)]:write_image(revdir/(name+'.png'),im)
            atomic_json(revdir/'result.json',payload)
            c.execute('INSERT INTO rounds(id,payload,synced) VALUES(?,?,0) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,synced=0',(ident,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
        atomic_json(folder/'result.json',payload)
        return folder,payload
    def sync(self,url,password):
        url=url.rstrip('/');s=requests.Session();r=s.post(url+'/api/manager/login',json={'password':password},timeout=15);r.raise_for_status()
        token=r.json()['token'];count=0
        try:
            with sqlite3.connect(self.db) as c:
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
