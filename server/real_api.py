"""Separate reviewed real rounds; legacy simulator scoring is never used here."""
import base64
import hashlib
import io
import json
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from PIL import Image
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'edge_real'))
from core import score_point,validate_config

class Upload(BaseModel):
    result:dict
    images:dict[str,str]


def install(app,require_manager,db_path,images_dir):
    @contextmanager
    def connect():
        c=sqlite3.connect(db_path);c.row_factory=sqlite3.Row
        try:
            c.execute('CREATE TABLE IF NOT EXISTS real_rounds(id TEXT PRIMARY KEY,created_at TEXT,payload TEXT,digest TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS real_round_history(id TEXT,revision INTEGER,payload TEXT,digest TEXT,PRIMARY KEY(id,revision))')
            c.commit()
            with c:yield c
        finally:c.close()
    @app.get('/real')
    def viewer():return FileResponse(Path(__file__).resolve().parents[1]/'viewer/real.html')
    @app.post('/api/real-rounds')
    def save(body:Upload,_:bool=Depends(require_manager)):
        p=body.result
        try:
            ident=str(uuid.UUID(p['id']))
            if ident!=p['id']:raise ValueError('Mã loạt không chuẩn')
            for k in ['shooter_code','shooter','unit','created_at']:
                if not isinstance(p[k],str) or not 0<len(p[k])<=200:raise ValueError('Thiếu thông tin '+k)
            if p.get('status','REVIEWED') not in ['AUTO','NEEDS_REVIEW','REVIEWED','DEMO']:raise ValueError('Trạng thái kết quả không hợp lệ')
            if p.get('status')=='REVIEWED' and not p.get('reviewed_at'):raise ValueError('Thiếu thời điểm kiểm tra')
            revision=p.get('revision',1)
            if type(revision) is not int or revision<1:raise ValueError('Phiên bản không hợp lệ')
            if int(p['lane'])<1 or int(p['series'])<1:raise ValueError('Làn/loạt không hợp lệ')
            if 'shooting_date' in p:date.fromisoformat(p['shooting_date'])
            if len(p['detections'])>1000:raise ValueError('Quá nhiều lỗ')
            validate_config(p['config'])
            if not p['config'].get('confirmed'):raise ValueError('Cấu hình chưa duyệt')
            for h in p['detections']:
                if not isinstance(h['selected'],bool):raise ValueError('Trạng thái lỗ sai')
                if h['score']!=score_point(h['template_point'],p['config']):raise ValueError('Điểm không khớp cấu hình')
            if p['total_score']!=sum(h['score'] for h in p['detections'] if h['selected']):raise ValueError('Tổng điểm sai')
            if p['total_holes']!=sum(h['selected'] for h in p['detections']):raise ValueError('Số lỗ sai')
            raw={}
            for name in ['before','after','annotated']:
                text=body.images[name]
                if len(text)>24_000_000:raise ValueError('Ảnh vượt 18 MB')
                data=base64.b64decode(text,validate=True)
                with Image.open(io.BytesIO(data)) as im:
                    if im.format!='PNG' or im.width*im.height>25_000_000:raise ValueError('Yêu cầu PNG <=25 megapixel')
                    im.verify()
                raw[name]=data
        except (ValueError,KeyError,TypeError,OverflowError,OSError) as e:raise HTTPException(422,str(e))
        canonical=json.dumps(p,sort_keys=True,ensure_ascii=False,allow_nan=False)
        digest=hashlib.sha256(canonical.encode()+b''.join(raw[n] for n in sorted(raw))).hexdigest()
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT digest,payload FROM real_rounds WHERE id=?',(ident,)).fetchone()
            if old:
                previous=json.loads(old['payload']);oldrev=previous.get('revision',1)
                if old['digest']==digest:return {'id':ident,'duplicate':True,'revision':oldrev}
                if revision<=oldrev:raise HTTPException(409,'Phiên bản này đã có nội dung khác hoặc đã cũ')
                for key in ['shooter_code','shooter','unit','lane','series','created_at']:
                    if p.get(key)!=previous.get(key):raise HTTPException(409,'Không đổi định danh người/loạt khi sửa điểm')
                if previous.get('shooting_date') and p.get('shooting_date')!=previous['shooting_date']:raise HTTPException(409,'Không đổi ngày bắn khi sửa điểm')
                c.execute('INSERT OR IGNORE INTO real_round_history VALUES(?,?,?,?)',(ident,oldrev,old['payload'],old['digest']))
            folder=Path(images_dir)/'real'/ident/f'rev_{revision}';folder.mkdir(parents=True,exist_ok=True)
            for name,data in raw.items():(folder/(name+'.png')).write_bytes(data)
            p=dict(p,image_urls={n:f'/images/real/{ident}/rev_{revision}/{n}.png' for n in raw})
            c.execute('INSERT INTO real_rounds VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,digest=excluded.digest',(ident,p['created_at'],json.dumps(p,ensure_ascii=False),digest))
        return {'id':ident,'duplicate':False,'revision':revision}
    @app.get('/api/real-rounds')
    def listing():
        with connect() as c:
            rows=c.execute('SELECT payload FROM real_rounds ORDER BY created_at DESC LIMIT 200').fetchall()
        keys=['id','created_at','shooting_date','shooter_code','shooter','unit','lane','series','total_score','total_holes','status','revision']
        return [{k:json.loads(r['payload']).get(k) for k in keys} for r in rows]
    @app.get('/api/real-rounds/{ident}')
    def detail(ident:str):
        with connect() as c:r=c.execute('SELECT payload FROM real_rounds WHERE id=?',(ident,)).fetchone()
        if not r:raise HTTPException(404,'Không tìm thấy loạt')
        return json.loads(r['payload'])
