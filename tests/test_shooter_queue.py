import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'edge_real'))
from shooter_queue import new_queue, validate_queue, next_pending, mark_scored

PEOPLE = [
    {'code': 'A01', 'name': 'Nguyễn Văn A', 'unit': 'Đơn vị 1'},
    {'code': 'A02', 'name': 'Trần Văn B', 'unit': 'Đơn vị 1'},
    {'code': 'A03', 'name': 'Lê Văn C', 'unit': 'Đơn vị 2'},
]


class QueueTest(unittest.TestCase):
    def test_keeps_file_order(self):
        q = new_queue(PEOPLE, 'danh_sach.csv')
        self.assertEqual([r['code'] for r in q['people']], ['A01','A02','A03'])
        self.assertEqual(q['active'], 0)

    def test_rejects_duplicate(self):
        with self.assertRaises(ValueError):
            new_queue(PEOPLE + [PEOPLE[0]])

    def test_next_not_yet_scored(self):
        q = new_queue(PEOPLE)
        q['completed'] = ['A02']
        self.assertEqual(next_pending(q), 2)
        q['active'] = 2
        self.assertIsNone(next_pending(q))

    def test_mark_only_current_and_idempotent(self):
        q = new_queue(PEOPLE)
        self.assertFalse(mark_scored(q, 'A02'))
        self.assertTrue(mark_scored(q, 'A01'))
        self.assertFalse(mark_scored(q, 'A01'))
        self.assertEqual(q['completed'], ['A01'])

    def test_rejects_invalid_row(self):
        with self.assertRaises(ValueError):
            new_queue([None])
        with self.assertRaises(ValueError):
            new_queue([{'code':'S01','name':'A','unit':None}])

    def test_validation(self):
        q = new_queue(PEOPLE)
        q['active'] = -1
        with self.assertRaises(ValueError):
            validate_queue(q)
        q['active'] = 0
        q['completed'] = ['missing']
        with self.assertRaises(ValueError):
            validate_queue(q)


if __name__ == '__main__':
    unittest.main()
