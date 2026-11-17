"""Separate reviewed real rounds; legacy simulator scoring is never used here."""
import base64
import copy
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from fastapi import Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'edge_real'))
from annotations import draw_markers, hole_labels, is_old, is_visible, markers
from core import read_image, rescore_result, score_point, transform, validate_config, write_image


class Upload(BaseModel):
    result: dict
    images: dict[str, str]


class EditRound(BaseModel):
    expected_revision: int
    action: str
    index: int | None = None
    point: list[float] | None = None
    radius: float | None = None
    classification: str | None = None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _resolve_round_folder(images_dir, ident, revision):
    base = Path(images_dir) / 'real' / ident
    rev_dir = base / f'rev_{revision}'
    return rev_dir if rev_dir.exists() else base


def _resolve_server_original(images_dir, ident, kind):
    orig_dir = Path(images_dir) / 'real' / ident / 'originals'
    if not orig_dir.exists():
        return None
    matches = sorted(orig_dir.glob(f'{kind}.*'))
    return matches[0] if matches else None


def _resolve_draft_original(ident, kind, drafts_root=None):
    base = Path(drafts_root) if drafts_root else (ROOT / 'data' / 'drafts')
    draft_dir = base / ident
    if not draft_dir.exists():
        return None
    matches = sorted(draft_dir.glob(f'{kind}_original.*'))
    return matches[0] if matches else None


def _save_original_image(images_dir, ident, kind, b64_text):
    if len(b64_text) > 24_000_000:
        raise ValueError('Ảnh gốc vượt 18 MB')
    data = base64.b64decode(b64_text, validate=True)
    with Image.open(io.BytesIO(data)) as im:
        fmt = (im.format or '').upper()
        if fmt not in ('PNG', 'JPEG', 'BMP') or im.width * im.height > 25_000_000:
            raise ValueError('Ảnh gốc phải là PNG/JPEG/BMP <= 25 megapixel')
        im.verify()
    if fmt == 'BMP':
        with Image.open(io.BytesIO(data)) as im:
            buf = io.BytesIO()
            im.convert('RGB').save(buf, format='PNG')
            data = buf.getvalue()
        ext = 'png'
    else:
        ext = 'jpg' if fmt == 'JPEG' else 'png'
    orig_dir = Path(images_dir) / 'real' / ident / 'originals'
    orig_dir.mkdir(parents=True, exist_ok=True)
    for old in orig_dir.glob(f'{kind}.*'):
        old.unlink(missing_ok=True)
    (orig_dir / f'{kind}.{ext}').write_bytes(data)
    return f'/images/real/{ident}/originals/{kind}.{ext}'


def _enrich_round(payload, images_dir, drafts_root=None):
    out = copy.deepcopy(payload)
    ident = str(out.get('id', ''))
    urls = dict(out.get('image_urls') or {})
    for kind in ('before', 'after'):
        key = f'{kind}_original'
        if not urls.get(key) and ident:
            srv = _resolve_server_original(images_dir, ident, kind)
            if srv is not None:
                urls[key] = f'/images/real/{ident}/originals/{srv.name}'
            elif _resolve_draft_original(ident, kind, drafts_root) is not None:
                urls[key] = f'/api/real-rounds/{ident}/original/{kind}'
    out['image_urls'] = urls
    detections = out.get('detections') or []
    out['hole_labels'] = hole_labels(detections)
    try:
        before_items = markers(out, 'before') if out.get('homography_before') else []
    except Exception:
        before_items = []
    if before_items:
        matched_map = {
            h.get('matched_before'): i
            for i, h in enumerate(detections)
            if h.get('matched_before') is not None and is_visible(h)
        }
        for item in before_items:
            item['matched_after'] = matched_map.get(item['index'])
    out['before_markers'] = before_items
    try:
        out['after_markers'] = markers(out, 'after') if out.get('homography_after') else []
    except Exception:
        out['after_markers'] = []
    if not out.get('image_size') and isinstance(out.get('config'), dict):
        out['image_size'] = out['config'].get('image_size')
    return out


def _apply_hole_edit(p, body: EditRound, width: int, height: int):
    action = body.action
    detections = p.setdefault('detections', [])
    if action in ('move', 'delete', 'restore', 'classify', 'toggle'):
        if body.index is None or not (0 <= body.index < len(detections)):
            raise ValueError('Chỉ số lỗ không hợp lệ')

    if action == 'move':
        if not body.point or len(body.point) != 2:
            raise ValueError('Thiếu tọa độ tâm lỗ')
        x, y = float(body.point[0]), float(body.point[1])
        if not np.isfinite([x, y]).all() or not (0 <= x < width and 0 <= y < height):
            raise ValueError('Tâm lỗ phải nằm trong ảnh sau')
        if body.radius is not None and (not np.isfinite(body.radius) or body.radius <= 0):
            raise ValueError('Bán kính vòng tròn phải dương')
        old = detections[body.index]
        tpt = transform([[x, y]], np.array(p['homography_after'], dtype=float))[0]
        prev_copy = copy.deepcopy(old)
        prev_copy.pop('previous', None)
        h = {
            'template_point': tpt,
            'original_point': [x, y],
            'score': score_point(tpt, p['config']),
            'selected': old['selected'],
            'confidence': 0.0,
            'source': 'manual',
            'matched_before': old.get('matched_before'),
            'issues': [],
            'review_edited': True,
            'excluded': False,
            'previous': prev_copy,
        }
        if 'classification' in old:
            h['classification'] = old['classification']
        radius = body.radius if body.radius is not None else old.get('manual_radius_original')
        if radius is not None:
            h['manual_radius_original'] = float(radius)
        detections[body.index] = h
    elif action == 'add':
        if not body.point or len(body.point) != 2:
            raise ValueError('Thiếu tọa độ tâm lỗ mới')
        x, y = float(body.point[0]), float(body.point[1])
        if not np.isfinite([x, y]).all() or not (0 <= x < width and 0 <= y < height):
            raise ValueError('Tâm lỗ mới phải nằm trong ảnh sau')
        if body.radius is not None and (not np.isfinite(body.radius) or body.radius <= 0):
            raise ValueError('Bán kính vòng tròn phải dương')
        tpt = transform([[x, y]], np.array(p['homography_after'], dtype=float))[0]
        h = {
            'template_point': tpt,
            'original_point': [x, y],
            'score': score_point(tpt, p['config']),
            'selected': True,
            'confidence': 0.0,
            'source': 'manual',
            'matched_before': None,
            'issues': [],
            'review_edited': True,
            'excluded': False,
        }
        if body.radius is not None:
            h['manual_radius_original'] = float(body.radius)
        detections.append(h)
    elif action == 'delete':
        h = detections[body.index]
        h['selected'] = False
        h['excluded'] = True
        h['review_edited'] = True
    elif action == 'restore':
        h = detections[body.index]
        h['excluded'] = False
        h['selected'] = not is_old(h)
        h['review_edited'] = True
    elif action == 'classify':
        if body.classification not in ('old', 'new'):
            raise ValueError('Chọn loại lỗ cũ (old) hoặc lỗ mới (new)')
        h = detections[body.index]
        h['classification'] = body.classification
        h['selected'] = body.classification == 'new'
        h['excluded'] = False
        h['review_edited'] = True
    elif action == 'toggle':
        h = detections[body.index]
        h['selected'] = not h['selected']
        h['review_edited'] = True
        if h['selected']:
            h['excluded'] = False
    elif action == 'review':
        p['review_warnings'] = list(p.get('warnings') or [])
        p['warnings'] = []
        p['status'] = 'DEMO' if p.get('demo') else 'REVIEWED'
        p['reviewed_at'] = _utc_now()
    else:
        raise ValueError('Thao tác chỉnh sửa không được hỗ trợ')

    if action != 'review':
        p['status'] = 'DEMO' if p.get('demo') else 'NEEDS_REVIEW'
        p.pop('reviewed_at', None)
        warnings = list(p.get('warnings') or [])
        msg = 'Kết quả đã sửa trên web, chưa xác nhận kiểm tra'
        if msg not in warnings:
            warnings.append(msg)
        p['warnings'] = warnings

    rescore_result(p)


def install(app, require_manager, db_path, images_dir, drafts_root=None):
    @contextmanager
    def connect():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        try:
            c.execute('CREATE TABLE IF NOT EXISTS real_rounds(id TEXT PRIMARY KEY,created_at TEXT,payload TEXT,digest TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS real_round_history(id TEXT,revision INTEGER,payload TEXT,digest TEXT,PRIMARY KEY(id,revision))')
            c.commit()
            with c:
                yield c
        finally:
            c.close()

    @app.get('/real')
    def viewer():
        return FileResponse(ROOT / 'viewer/real.html')

    @app.post('/api/real-rounds')
    def save(body: Upload, _: bool = Depends(require_manager)):
        p = body.result
        try:
            ident = str(uuid.UUID(p['id']))
            if ident != p['id']:
                raise ValueError('Mã loạt không chuẩn')
            for k in ['shooter_code', 'shooter', 'unit', 'created_at']:
                if not isinstance(p[k], str) or not 0 < len(p[k]) <= 200:
                    raise ValueError('Thiếu thông tin ' + k)
            if p.get('status', 'REVIEWED') not in ['AUTO', 'NEEDS_REVIEW', 'REVIEWED', 'DEMO']:
                raise ValueError('Trạng thái kết quả không hợp lệ')
            if p.get('status') == 'REVIEWED' and not p.get('reviewed_at'):
                raise ValueError('Thiếu thời điểm kiểm tra')
            revision = p.get('revision', 1)
            if type(revision) is not int or revision < 1:
                raise ValueError('Phiên bản không hợp lệ')
            if int(p['lane']) < 1 or int(p['series']) < 1:
                raise ValueError('Làn/loạt không hợp lệ')
            if 'shooting_date' in p:
                date.fromisoformat(p['shooting_date'])
            if len(p['detections']) > 1000:
                raise ValueError('Quá nhiều lỗ')
            validate_config(p['config'])
            if not p['config'].get('confirmed'):
                raise ValueError('Cấu hình chưa duyệt')
            for h in p['detections']:
                if not isinstance(h['selected'], bool):
                    raise ValueError('Trạng thái lỗ sai')
                if h['score'] != score_point(h['template_point'], p['config']):
                    raise ValueError('Điểm không khớp cấu hình')
            if p['total_score'] != sum(h['score'] for h in p['detections'] if h['selected']):
                raise ValueError('Tổng điểm sai')
            if p['total_holes'] != sum(h['selected'] for h in p['detections']):
                raise ValueError('Số lỗ sai')
            raw = {}
            for name in ['before', 'after', 'annotated']:
                text = body.images[name]
                if len(text) > 24_000_000:
                    raise ValueError('Ảnh vượt 18 MB')
                data = base64.b64decode(text, validate=True)
                with Image.open(io.BytesIO(data)) as im:
                    if im.format != 'PNG' or im.width * im.height > 25_000_000:
                        raise ValueError('Yêu cầu PNG <=25 megapixel')
                    im.verify()
                raw[name] = data
            orig_urls = {}
            for kind in ('before', 'after'):
                key = f'{kind}_original'
                if body.images.get(key):
                    orig_urls[key] = _save_original_image(images_dir, ident, kind, body.images[key])
                else:
                    existing_orig = _resolve_server_original(images_dir, ident, kind)
                    if existing_orig is not None:
                        orig_urls[key] = f'/images/real/{ident}/originals/{existing_orig.name}'
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as e:
            raise HTTPException(422, str(e))

        canonical = json.dumps(p, sort_keys=True, ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(canonical.encode() + b''.join(raw[n] for n in sorted(raw))).hexdigest()
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT digest,payload FROM real_rounds WHERE id=?', (ident,)).fetchone()
            if old:
                previous = json.loads(old['payload'])
                oldrev = previous.get('revision', 1)
                if old['digest'] == digest:
                    return {'id': ident, 'duplicate': True, 'revision': oldrev}
                if revision <= oldrev:
                    raise HTTPException(409, 'Phiên bản này đã có nội dung khác hoặc đã cũ')
                for key in ['shooter_code', 'shooter', 'unit', 'lane', 'series', 'created_at']:
                    if p.get(key) != previous.get(key):
                        raise HTTPException(409, 'Không đổi định danh người/loạt khi sửa điểm')
                if previous.get('shooting_date') and p.get('shooting_date') != previous['shooting_date']:
                    raise HTTPException(409, 'Không đổi ngày bắn khi sửa điểm')
                c.execute('INSERT OR IGNORE INTO real_round_history VALUES(?,?,?,?)', (ident, oldrev, old['payload'], old['digest']))
            folder = Path(images_dir) / 'real' / ident / f'rev_{revision}'
            folder.mkdir(parents=True, exist_ok=True)
            for name, data in raw.items():
                (folder / (name + '.png')).write_bytes(data)
            image_urls = {n: f'/images/real/{ident}/rev_{revision}/{n}.png' for n in raw}
            image_urls.update(orig_urls)
            p = dict(p, image_urls=image_urls)
            c.execute(
                'INSERT INTO real_rounds VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,digest=excluded.digest',
                (ident, p['created_at'], json.dumps(p, ensure_ascii=False), digest),
            )
        return {'id': ident, 'duplicate': False, 'revision': revision}

    @app.get('/api/real-rounds')
    def listing():
        with connect() as c:
            rows = c.execute('SELECT payload FROM real_rounds ORDER BY created_at DESC LIMIT 1000').fetchall()
        keys = [
            'id', 'created_at', 'shooting_date', 'before_acquired_at', 'after_acquired_at',
            'processed_at', 'updated_at', 'reviewed_at', 'shooter_code', 'shooter', 'unit',
            'lane', 'series', 'expected_shots', 'total_score', 'total_holes', 'status',
            'revision', 'notes',
        ]
        result = []
        for r in rows:
            p = json.loads(r['payload'])
            item = {k: p.get(k) for k in keys}
            item['warnings_count'] = len(p.get('warnings') or [])
            result.append(item)
        return result

    @app.get('/api/real-rounds/{ident}')
    def detail(ident: str):
        with connect() as c:
            r = c.execute('SELECT payload FROM real_rounds WHERE id=?', (ident,)).fetchone()
        if not r:
            raise HTTPException(404, 'Không tìm thấy loạt')
        return _enrich_round(json.loads(r['payload']), images_dir, drafts_root)

    @app.get('/api/real-rounds/{ident}/original/{kind}')
    def get_original(ident: str, kind: str):
        if kind not in ('before', 'after'):
            raise HTTPException(404, 'Loại ảnh không hợp lệ')
        try:
            clean_id = str(uuid.UUID(ident))
        except ValueError:
            raise HTTPException(404, 'Mã loạt không hợp lệ')
        path = _resolve_server_original(images_dir, clean_id, kind) or _resolve_draft_original(clean_id, kind, drafts_root)
        if path is None or not path.exists():
            raise HTTPException(404, 'Chưa có ảnh gốc')
        if path.suffix.lower() == '.bmp':
            with Image.open(path) as im:
                buf = io.BytesIO()
                im.convert('RGB').save(buf, format='PNG')
            return Response(content=buf.getvalue(), media_type='image/png')
        return FileResponse(path)

    @app.post('/api/real-rounds/{ident}/edit')
    def edit_round(ident: str, body: EditRound, _: bool = Depends(require_manager)):
        try:
            clean_id = str(uuid.UUID(ident))
        except ValueError:
            raise HTTPException(404, 'Mã loạt không hợp lệ')
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT digest,payload FROM real_rounds WHERE id=?', (clean_id,)).fetchone()
            if not old:
                raise HTTPException(404, 'Không tìm thấy loạt')
            previous = json.loads(old['payload'])
            oldrev = previous.get('revision', 1)
            if body.expected_revision != oldrev:
                raise HTTPException(409, 'Loạt đã được sửa ở nơi khác. Bấm Làm mới trước khi sửa tiếp.')

            old_folder = _resolve_round_folder(images_dir, clean_id, oldrev)
            after_path = old_folder / 'after.png'
            before_path = old_folder / 'before.png'
            if not after_path.exists() or not before_path.exists():
                raise HTTPException(404, 'Thiếu file ảnh của loạt trên server')

            after_im = read_image(after_path)
            height, width = after_im.shape[:2]
            p = copy.deepcopy(previous)
            try:
                validate_config(p['config'])
                if body.action in ('move', 'add') and not p.get('homography_after'):
                    raise ValueError('Loạt chưa có ma trận căn chỉnh ảnh sau')
                _apply_hole_edit(p, body, width, height)
            except (ValueError, KeyError, TypeError) as e:
                raise HTTPException(422, str(e))

            revision = oldrev + 1
            p['revision'] = revision
            p['updated_at'] = _utc_now()
            new_folder = Path(images_dir) / 'real' / clean_id / f'rev_{revision}'
            new_folder.mkdir(parents=True, exist_ok=True)
            for fname in ('before.png', 'after.png'):
                shutil.copy2(old_folder / fname, new_folder / fname)
            annotated_im = draw_markers(after_im, markers(p, 'after'))
            write_image(new_folder / 'annotated.png', annotated_im)

            urls = dict(p.get('image_urls') or {})
            for name in ('before', 'after', 'annotated'):
                urls[name] = f'/images/real/{clean_id}/rev_{revision}/{name}.png'
            p['image_urls'] = urls

            c.execute('INSERT OR IGNORE INTO real_round_history VALUES(?,?,?,?)', (clean_id, oldrev, old['payload'], old['digest']))
            digest_payload = {k: v for k, v in p.items() if k != 'image_urls'}
            canonical = json.dumps(digest_payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
            raw_bytes = b''.join((new_folder / f'{n}.png').read_bytes() for n in ('annotated', 'after', 'before'))
            digest = hashlib.sha256(canonical.encode() + raw_bytes).hexdigest()
            c.execute(
                'UPDATE real_rounds SET payload=?, digest=? WHERE id=?',
                (json.dumps(p, ensure_ascii=False), digest, clean_id),
            )
        return _enrich_round(p, images_dir, drafts_root)
