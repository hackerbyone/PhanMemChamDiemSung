import base64
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'edge_real'))
sys.path.insert(0, str(ROOT / 'server'))

from core import read_image, rescore_result
from real_api import install as install_real_api
from storage import Store, utc
from template_geometry import load_or_extract


def png_b64(im):
    ok, buf = cv2.imencode('.png', im)
    assert ok
    return base64.b64encode(buf.tobytes()).decode('ascii')


class RealWebApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'sample_check')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'server.db'
        self.images_dir = self.root / 'images'
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.drafts_dir = self.root / 'drafts'
        self.drafts_dir.mkdir(parents=True, exist_ok=True)

        app = FastAPI()
        app.mount('/images', StaticFiles(directory=str(self.images_dir)), name='images')

        def require_manager(x_manager_token: str | None = Header(default=None)):
            if x_manager_token != 'secret-token':
                raise HTTPException(401, 'manager login required')
            return True

        install_real_api(app, require_manager, str(self.db), str(self.images_dir), drafts_root=self.drafts_dir)
        self.client = TestClient(app)

        self.template = read_image(ROOT / 'assets/Biaso4.png')
        self.config = load_or_extract(ROOT / 'assets/Biaso4.png', self.root / 'target_config.json')
        self.round_id = str(uuid.uuid4())
        self.payload = {
            'id': self.round_id,
            'shooter_code': 'NB0001',
            'shooter': 'Nguyễn Văn An',
            'unit': 'Đơn vị 01',
            'lane': 1,
            'series': 2,
            'shooting_date': '2026-11-17',
            'created_at': utc(),
            'before_acquired_at': utc(),
            'after_acquired_at': utc(),
            'status': 'AUTO',
            'revision': 1,
            'config': copy.deepcopy(self.config),
            'homography_before': np.eye(3).tolist(),
            'homography_after': np.eye(3).tolist(),
            'before_detections': [{'template_point': [450.0, 450.0], 'confidence': 0.9}],
            'detections': [
                {
                    'template_point': [450.0, 450.0],
                    'original_point': [450.0, 450.0],
                    'matched_before': 0,
                    'selected': False,
                    'score': 10,
                    'confidence': 0.9,
                    'source': 'model',
                    'issues': [],
                },
                {
                    'template_point': [451.0, 453.0],
                    'original_point': [451.0, 453.0],
                    'matched_before': None,
                    'selected': True,
                    'score': 10,
                    'confidence': 0.88,
                    'source': 'model',
                    'issues': [],
                },
            ],
            'warnings': [],
        }
        rescore_result(self.payload)
        self.b64_template = png_b64(self.template)

    def _upload(self, extra_images=None):
        images = {'before': self.b64_template, 'after': self.b64_template, 'annotated': self.b64_template}
        if extra_images:
            images.update(extra_images)
        return self.client.post(
            '/api/real-rounds',
            json={'result': self.payload, 'images': images},
            headers={'X-Manager-Token': 'secret-token'},
        )

    def test_real_viewer_page_has_required_controls(self):
        resp = self.client.get('/real')
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        for token in ('fQuery', 'fScore', 'roundsBody', 'beforeCanvas', 'afterCanvas', 'btnOrigBefore', 'btnOrigAfter', 'holesBody', 'holeModal', 'origModal'):
            self.assertIn(token, html)

    def test_upload_originals_and_fallback_draft_originals(self):
        # 1) Upload with original images (including BMP)
        ok, bmp_buf = cv2.imencode('.bmp', self.template[:200, :200])
        self.assertTrue(ok)
        bmp_b64 = base64.b64encode(bmp_buf.tobytes()).decode('ascii')
        res = self._upload({'before_original': bmp_b64, 'after_original': self.b64_template})
        self.assertEqual(res.status_code, 200, res.text)

        detail = self.client.get(f'/api/real-rounds/{self.round_id}').json()
        self.assertTrue(detail['image_urls']['before_original'].endswith('/originals/before.png'))
        self.assertTrue(detail['image_urls']['after_original'].endswith('/originals/after.png'))
        self.assertEqual(len(detail['before_markers']), 1)
        self.assertEqual(len(detail['after_markers']), 2)
        self.assertEqual(detail['hole_labels'], ['C1', 'M1'])

        orig_resp = self.client.get(detail['image_urls']['before_original'])
        self.assertEqual(orig_resp.status_code, 200)

        # 2) Fallback from local draft dir for older round without uploaded originals
        old_id = str(uuid.uuid4())
        self.payload['id'] = old_id
        self.round_id = old_id
        res2 = self._upload()
        self.assertEqual(res2.status_code, 200)
        draft_dir = self.drafts_dir / old_id
        draft_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(draft_dir / 'before_original.bmp'), self.template[:150, :150])
        detail2 = self.client.get(f'/api/real-rounds/{old_id}').json()
        self.assertEqual(detail2['image_urls']['before_original'], f'/api/real-rounds/{old_id}/original/before')
        self.assertIsNone(detail2['image_urls'].get('after_original'))
        fallback_resp = self.client.get(detail2['image_urls']['before_original'])
        self.assertEqual(fallback_resp.status_code, 200)
        self.assertEqual(fallback_resp.headers['content-type'], 'image/png')

    def test_edit_move_add_classify_toggle_delete_restore_and_review(self):
        self.assertEqual(self._upload().status_code, 200)

        # Auth check
        unauth = self.client.post(f'/api/real-rounds/{self.round_id}/edit', json={'expected_revision': 1, 'action': 'review'})
        self.assertEqual(unauth.status_code, 401)

        # Listing includes timestamps & score
        listed = self.client.get('/api/real-rounds').json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['total_score'], 10)
        self.assertIsNotNone(listed[0]['before_acquired_at'])

        # Add new hole at center (10 pts) -> total 20, rev 2
        r2 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 1, 'action': 'add', 'point': [450.0, 450.0], 'radius': 15.0},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        d2 = r2.json()
        self.assertEqual(d2['revision'], 2)
        self.assertEqual(d2['total_score'], 20)
        self.assertEqual(d2['total_holes'], 2)
        self.assertEqual(d2['status'], 'NEEDS_REVIEW')
        self.assertEqual(d2['hole_labels'], ['C1', 'M1', 'M2'])
        self.assertTrue((self.images_dir / 'real' / self.round_id / 'rev_2' / 'annotated.png').exists())

        # Stale revision -> 409
        stale = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 1, 'action': 'toggle', 'index': 1},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(stale.status_code, 409)

        # Out-of-bounds point -> 422
        bad_pt = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 2, 'action': 'move', 'index': 2, 'point': [9999.0, 10.0]},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(bad_pt.status_code, 422)

        # Move hole 2 to corner [5, 5] (0 pts) -> total 10, rev 3
        r3 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 2, 'action': 'move', 'index': 2, 'point': [5.0, 5.0]},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()['total_score'], 10)
        self.assertEqual(r3.json()['detections'][2]['score'], 0)

        # Classify hole 1 as 'old' (red, selected=False) -> total 0, rev 4
        r4 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 3, 'action': 'classify', 'index': 1, 'classification': 'old'},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r4.status_code, 200)
        self.assertEqual(r4.json()['total_score'], 0)
        self.assertEqual(r4.json()['hole_labels'], ['C1', 'C2', 'M1'])

        # Classify hole 1 back to 'new' -> total 10, rev 5
        r5 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 4, 'action': 'classify', 'index': 1, 'classification': 'new'},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r5.status_code, 200)
        self.assertEqual(r5.json()['total_score'], 10)
        self.assertEqual(r5.json()['hole_labels'], ['C1', 'M1', 'M2'])

        # Delete hole 1 -> label '—', total 0, rev 6
        r6 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 5, 'action': 'delete', 'index': 1},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r6.status_code, 200)
        self.assertEqual(r6.json()['hole_labels'], ['C1', '—', 'M1'])
        self.assertEqual(r6.json()['total_score'], 0)

        # Restore hole 1 -> total 10, rev 7
        r7 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 6, 'action': 'restore', 'index': 1},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r7.status_code, 200)
        self.assertEqual(r7.json()['total_score'], 10)

        # Review -> REVIEWED, warnings cleared, rev 8
        r8 = self.client.post(
            f'/api/real-rounds/{self.round_id}/edit',
            json={'expected_revision': 7, 'action': 'review'},
            headers={'X-Manager-Token': 'secret-token'},
        )
        self.assertEqual(r8.status_code, 200)
        d8 = r8.json()
        self.assertEqual(d8['status'], 'REVIEWED')
        self.assertEqual(d8['warnings'], [])
        self.assertIsNotNone(d8['reviewed_at'])
        with closing(sqlite3.connect(self.db)) as c:
            hist_count = c.execute('SELECT COUNT(*) FROM real_round_history WHERE id=?', (self.round_id,)).fetchone()[0]
        self.assertEqual(hist_count, 7)

    def test_store_sync_uploads_original_images(self):
        store = Store(self.root / 'edge_data')
        _, saved = store.save({'detections': [], 'config': self.config, 'homography_before': np.eye(3).tolist(), 'homography_after': np.eye(3).tolist()}, {
            'shooter_code': 'S01', 'shooter': 'Tester', 'unit': 'U1', 'lane': 1, 'series': 1, 'shooting_date': '2026-11-17',
        }, self.template, self.template, self.template)
        draft_dir = store.root / 'drafts' / saved['id']
        draft_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(draft_dir / 'before_original.bmp'), self.template[:120, :120])

        class FakeResp:
            def __init__(self, data):
                self._data = data
            def raise_for_status(self):
                pass
            def json(self):
                return self._data

        sent_payloads = []
        class FakeSession:
            def post(self, url, json=None, headers=None, timeout=None):
                if url.endswith('/api/manager/login'):
                    return FakeResp({'token': 'tok'})
                if url.endswith('/api/real-rounds'):
                    sent_payloads.append(json)
                    return FakeResp({'id': json['result']['id'], 'duplicate': False, 'revision': 1})
                return FakeResp({'ok': True})
            def close(self):
                pass

        import storage
        orig_sess = storage.requests.Session
        storage.requests.Session = FakeSession
        try:
            count = store.sync('http://127.0.0.1:8000', 'pw')
        finally:
            storage.requests.Session = orig_sess
        self.assertEqual(count, 1)
        self.assertIn('before_original', sent_payloads[0]['images'])
        self.assertNotIn('after_original', sent_payloads[0]['images'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
