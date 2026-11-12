import sys
import tempfile
import unittest
from pathlib import Path
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'edge_real'))
from shooter_queue import ShooterQueue, new_queue, validate_queue, next_pending, mark_scored

PEOPLE = [
    {'code': 'A01', 'name': 'Nguyễn Văn A', 'unit': 'Đơn vị 1'},
    {'code': 'A02', 'name': 'Trần Văn B', 'unit': 'Đơn vị 1'},
    {'code': 'A03', 'name': 'Lê Văn C', 'unit': 'Đơn vị 2'},
]


class DummyStore:
    def __init__(self, root, shooters=None, rounds=None):
        self.root = Path(root)
        self._shooters = list(shooters or [])
        self._rounds = list(rounds or [])

    def list_shooters(self, query=''):
        return list(self._shooters)

    def list_rounds(self, **kwargs):
        return list(self._rounds)

    def save_shooters(self, people, replace=False):
        self._shooters = list(people)
        return len(people)


class DummyApp(ShooterQueue, tk.Tk):
    def __init__(self, store):
        super().__init__()
        self.withdraw()
        self.store = store
        self.before_image = None
        self.result = None
        self.config = {'confirmed': True}
        self.queue_header = tk.StringVar(value='CHƯA CHỌN NGƯỜI BẮN')
        self.summary = tk.StringVar(value='Chọn người bắn và chụp ảnh trước.')
        self.status = tk.StringVar()
        self.vars = {
            'shooter_code': tk.StringVar(),
            'shooter': tk.StringVar(),
            'unit': tk.StringVar(),
        }
        from tkinter import ttk
        self.notebook = ttk.Notebook(self)
        self.tab0 = ttk.Frame(self.notebook)
        self.notebook.add(self.tab0, text='Loạt bắn')
        self.build_queue_panel(self.tab0)
        self.queue_restore()

    def button(self, parent, label, func):
        from tkinter import ttk
        return ttk.Button(parent, text=label, command=func)

    def guard(self, func):
        func()

    def reset(self, new_shooter=False):
        if new_shooter:
            for k in ('shooter_code', 'shooter', 'unit'):
                self.vars[k].set('')
        self.queue_update_header()
        return True


class QueueTest(unittest.TestCase):
    def test_keeps_file_order(self):
        q = new_queue(PEOPLE, 'danh_sach.csv')
        self.assertEqual([r['code'] for r in q['people']], ['A01', 'A02', 'A03'])
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
            new_queue([{'code': 'S01', 'name': 'A', 'unit': None}])

    def test_validation(self):
        q = new_queue(PEOPLE)
        q['active'] = -1
        with self.assertRaises(ValueError):
            validate_queue(q)
        q['active'] = 0
        q['completed'] = ['missing']
        with self.assertRaises(ValueError):
            validate_queue(q)

    def test_ui_selection_from_form_and_queue_table_updates_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DummyStore(tmp, shooters=PEOPLE)
            app = DummyApp(store)
            try:
                # Restores from store when shooting_queue.json does not exist yet
                self.assertEqual(app.queue_header.get(), 'ĐANG TỚI LƯỢT 1/3: Nguyễn Văn A (A01)')
                # Single-selecting a row in the queue table updates header & form
                app.queue_table.selection_set('1')
                app.queue_on_table_select()
                self.assertEqual(app.vars['shooter_code'].get(), 'A02')
                self.assertEqual(app.vars['shooter'].get(), 'Trần Văn B')
                self.assertEqual(app.queue_header.get(), 'ĐANG TỚI LƯỢT 2/3: Trần Văn B (A02)')
                self.assertIn('Trần Văn B (A02)', app.summary.get())
                # Typing a code in the form selects that shooter and updates header
                app.vars['shooter_code'].set('A03')
                self.assertEqual(app.vars['shooter'].get(), 'Lê Văn C')
                self.assertEqual(app.queue_header.get(), 'ĐANG TỚI LƯỢT 3/3: Lê Văn C (A03)')
                # Typing a manual shooter not in queue still updates header instead of CHƯA CHỌN NGƯỜI BẮN
                app.vars['shooter_code'].set('S99')
                app.vars['shooter'].set('Lê Trí Dũng')
                app.vars['unit'].set('CNTT1')
                self.assertEqual(app.queue_header.get(), 'NGƯỜI BẮN: Lê Trí Dũng (S99)')
            finally:
                app.destroy()


if __name__ == '__main__':
    unittest.main()
