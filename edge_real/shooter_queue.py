"""Ordered local shooting queue, extending the existing roster and capture UI.

The queue is distinct from the application's shooter database. A queued person
represents a turn on this computer; completion means at least one real result
has been saved, not that the score is approved.
"""
import copy
import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from storage import atomic_json


def new_queue(people, source=''):
    if not isinstance(people, list) or not people:
        raise ValueError('Danh sách lượt bắn phải là một danh sách không rỗng')
    cleaned, seen = [], set()
    for idx, row in enumerate(people, 1):
        if not isinstance(row, dict):
            raise ValueError(f'Dòng {idx}: thông tin người bắn không hợp lệ')
        person = {key: str(row.get(key) or '').strip() for key in ('code', 'name', 'unit')}
        if not all(person.values()) or any(len(value) > 200 for value in person.values()):
            raise ValueError(f'Dòng {idx}: cần mã, họ tên, đơn vị (1–200 ký tự)')
        if person['code'] in seen:
            raise ValueError(f'Mã người bắn trùng trong danh sách: {person["code"]}')
        seen.add(person['code'])
        cleaned.append(person)
    return {'version': 1, 'source': str(source), 'people': cleaned, 'active': 0, 'completed': []}


def validate_queue(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Định dạng dữ liệu thứ tự lượt không hợp lệ')
    checked = new_queue(data.get('people', []), data.get('source', ''))
    active = data.get('active')
    if not isinstance(active, int) or isinstance(active, bool) or not 0 <= active < len(checked['people']):
        raise ValueError('Vị trí lượt hiện tại không hợp lệ')
    codes = {person['code'] for person in checked['people']}
    completed = data.get('completed', [])
    if not isinstance(completed, list) or not all(isinstance(code, str) and code in codes for code in completed):
        raise ValueError('Danh sách lượt đã chấm không hợp lệ')
    checked['active'] = active
    checked['completed'] = list(dict.fromkeys(completed))
    return checked


def next_pending(data):
    """Only go forward. Skipped turns can be selected manually later."""
    if not data:
        return None
    completed = set(data['completed'])
    return next((i for i in range(data['active'] + 1, len(data['people']))
                 if data['people'][i]['code'] not in completed), None)


def mark_scored(data, code):
    """Mark a real saved result only for the explicitly active shooter."""
    if not data or data['people'][data['active']]['code'] != code:
        return False
    if code not in data['completed']:
        data['completed'].append(code)
        return True
    return False


class ShooterQueue:
    """Tkinter mixin for the current app, independent of the scoring engine."""

    def build_queue_panel(self, parent):
        self.queue_path = self.store.root / 'shooting_queue.json'
        self.queue_state = None
        panel = ttk.LabelFrame(parent, text='THỨ TỰ NGƯỜI BẮN — theo file CSV trên máy')
        panel.pack(fill='x', padx=5, pady=(7, 9))
        toolbar = ttk.Frame(panel)
        toolbar.pack(fill='x', padx=6, pady=4)
        self.button(toolbar, 'Nạp danh sách CSV', self.queue_import_csv).pack(side='left', padx=3)
        self.button(toolbar, 'Chọn người đang bắn', self.queue_choose_selected).pack(side='left', padx=3)
        self.button(toolbar, 'Người tiếp theo', self.queue_next).pack(side='left', padx=3)
        self.queue_count = tk.StringVar(value='Chưa nạp danh sách')
        ttk.Label(toolbar, textvariable=self.queue_count).pack(side='right', padx=8)
        table_frame = ttk.Frame(panel)
        table_frame.pack(fill='x', padx=6, pady=(0, 5))
        cols = [('no', 'STT', 55), ('code', 'Mã', 120), ('name', 'Họ tên', 240),
                ('unit', 'Đơn vị', 200), ('state', 'Trạng thái', 160)]
        self.queue_table = ttk.Treeview(table_frame, columns=[v[0] for v in cols],
                                        show='headings', height=5, selectmode='browse')
        for key, label, width in cols:
            self.queue_table.heading(key, text=label)
            self.queue_table.column(key, width=width, minwidth=50)
        self.queue_table.tag_configure('active', background='#FFF1A8', foreground='#1F2937')
        self.queue_table.tag_configure('scored', background='#E5F7E9', foreground='#22563A')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.queue_table.yview)
        self.queue_table.configure(yscrollcommand=scroll.set)
        self.queue_table.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        self.queue_table.bind('<Double-1>', lambda event: self.guard(self.queue_choose_selected))

    def queue_restore(self):
        if self.queue_path.exists():
            try:
                self.queue_state = validate_queue(json.loads(self.queue_path.read_text(encoding='utf-8')))
                self.queue_apply_identity()
            except (ValueError, TypeError, OSError) as exc:
                self.queue_state = None
                messagebox.showwarning('Danh sách người bắn',
                    f'Không đọc được danh sách lượt đã lưu: {exc}\nCó thể nạp lại CSV.')
        self.queue_refresh()

    def queue_save(self):
        if self.queue_state:
            atomic_json(self.queue_path, self.queue_state)

    def queue_refresh(self):
        self.queue_table.delete(*self.queue_table.get_children())
        if not self.queue_state:
            self.queue_count.set('Chưa nạp danh sách')
            self.queue_header.set('CHƯA CHỌN NGƯỜI BẮN')
            return
        state = self.queue_state
        active = state['active']
        scored = set(state['completed'])
        for index, person in enumerate(state['people']):
            is_active = index == active
            done = person['code'] in scored
            label = ('ĐANG TỚI LƯỢT • ĐÃ CHẤM' if done else '▶ ĐANG TỚI LƯỢT') if is_active else ('ĐÃ CHẤM' if done else 'CHỜ')
            tags = ('active',) if is_active else (('scored',) if done else ())
            self.queue_table.insert('', 'end', iid=str(index),
                values=(index + 1, person['code'], person['name'], person['unit'], label), tags=tags)
        self.queue_table.selection_set(str(active))
        self.queue_table.focus(str(active))
        self.queue_table.see(str(active))
        person = state['people'][active]
        self.queue_header.set(f'ĐANG TỚI LƯỢT {active+1}/{len(state["people"])}: {person["name"]} ({person["code"]})')
        self.queue_count.set(f'Đã chấm: {len(scored)}/{len(state["people"])} • File: {state["source"]}')

    def queue_apply_identity(self):
        if not self.queue_state or self.before_image is not None:
            return
        person = self.queue_state['people'][self.queue_state['active']]
        for field, key in (('shooter_code', 'code'), ('shooter', 'name'), ('unit', 'unit')):
            self.vars[field].set(person[key])

    def queue_switch(self, index):
        if not self.queue_state:
            raise ValueError('Nạp danh sách CSV trước khi chọn lượt')
        if not 0 <= index < len(self.queue_state['people']):
            raise ValueError('Vị trí người bắn không hợp lệ')
        # Existing reset is responsible for protecting unfinished captures.
        if self.before_image is not None and not self.reset(new_shooter=True):
            return False
        self.queue_state['active'] = index
        self.queue_save()
        self.queue_apply_identity()
        self.queue_refresh()
        self.notebook.select(0)
        self.status.set('Đã chuyển người bắn. Kiểm tra bệ/loạt trước khi chụp TRƯỚC.')
        return True

    def queue_choose_selected(self):
        selected = self.queue_table.selection()
        if len(selected) != 1:
            raise ValueError('Chọn đúng một dòng trong danh sách lượt')
        self.queue_switch(int(selected[0]))

    def queue_next(self):
        if not self.queue_state:
            raise ValueError('Chưa có danh sách lượt bắn; nạp CSV trước')
        index = next_pending(self.queue_state)
        if index is None:
            messagebox.showinfo('Danh sách lượt', 'Không còn lượt CHỜ phía sau. Chọn trực tiếp một dòng nếu cần quay lại.')
            return
        self.queue_switch(index)

    def queue_next_or_reset(self):
        if self.queue_state:
            self.queue_next()
        else:
            self.reset(new_shooter=True)

    def queue_import_csv(self):
        if self.before_image is not None:
            raise ValueError('Bấm Loạt tiếp theo / Người tiếp theo trước khi thay danh sách đang dùng')
        path = filedialog.askopenfilename(filetypes=[('Danh sách CSV UTF-8', '*.csv')])
        if not path:
            return
        # Reuse the CSV parser and validation of the latest repository.
        from reports import import_roster
        people = import_roster(path)
        next_state = new_queue(people, Path(path).name)
        if self.queue_state and not messagebox.askyesno('Thay danh sách lượt',
                'Thay danh sách hiện tại sẽ đặt lại người đang tới lượt và các dấu đã chấm. Tiếp tục?'):
            return
        # Insert people into the pre-existing roster database (safe on repeated import).
        self.store.save_shooters(people)
        self.queue_state = next_state
        self.queue_save()
        self.queue_apply_identity()
        self.queue_refresh()
        self.refresh_roster()
        self.status.set(f'Đã nạp {len(people)} người theo đúng thứ tự file {Path(path).name}.')

    def queue_select_by_code(self, code):
        if not self.queue_state:
            return
        for idx, person in enumerate(self.queue_state['people']):
            if person['code'] == code:
                self.queue_state['active'] = idx
                self.queue_save()
                self.queue_refresh()
                return

    def queue_check_before(self, source):
        if source == 'reference_template_demo' or not self.queue_state:
            return
        person = self.queue_state['people'][self.queue_state['active']]
        if self.meta['shooter_code'] != person['code'] or self.meta['shooter'] != person['name'] or self.meta['unit'] != person['unit']:
            raise ValueError('Người trong ô nhập chưa khớp người đang tới lượt. Bấm Chọn người đang bắn.')

    def queue_on_scored(self):
        if not self.queue_state or not self.meta or self.meta.get('demo'):
            return
        if mark_scored(self.queue_state, self.meta.get('shooter_code')):
            self.queue_save()
            self.queue_refresh()
