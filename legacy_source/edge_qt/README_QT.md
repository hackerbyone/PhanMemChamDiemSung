# Edge Simulator (Qt/C++)

A second implementation of the edge simulator, alongside the existing Python
`edge/edge.py` -- **not a replacement**. Same wire protocol, same local
SQLite schema, same "edge N simulates lane N" convention, same firing-order
logic, now with a small GUI that shows the target image updating live after
every shot. Use whichever implementation you like; they can even run side by
side on different lanes, since the server only cares about `lane_number`,
never which program produced it.

## Prerequisites (Windows)

- **Qt 6** (6.4 or newer) with the **Widgets**, **Network**, and **SQL**
  modules -- these are included in Qt Creator's default installer selection.
  Get it from the Qt Online Installer: https://www.qt.io/download-qt-installer
  Pick either an MSVC kit (needs Visual Studio's "Desktop development with
  C++" workload) or a MinGW kit (the Qt installer can install MinGW for you
  too -- simplest if you don't already have Visual Studio).
- **CMake 3.16+** -- bundled with Qt Creator, or https://cmake.org/download/
  if you build from the command line instead.

## Build & run -- Qt Creator (recommended)

1. Qt Creator -> **File -> Open File or Project...** -> select
   `edge_qt/CMakeLists.txt`.
2. When prompted, pick a **Kit** matching the Qt version/compiler you
   installed (e.g. "Desktop Qt 6.7.0 MinGW 64-bit").
3. Build (**Ctrl+B**), then Run (**Ctrl+R**).
4. To pass command-line options (see below), use **Projects** (left sidebar)
   -> **Run** -> **Command line arguments**, e.g.:
   `--server http://192.168.50.124:8000 --lane 1 --targets 3 --shots 5`

## Build & run -- command line

MSVC:
```
cd edge_qt
cmake -B build -G "Visual Studio 17 2022" -A x64 -DCMAKE_PREFIX_PATH="C:/Qt/6.7.0/msvc2019_64"
cmake --build build --config Release
build\Release\edge_qt.exe --server http://SERVER_IP:8000 --lane 1 --targets 2 --shots 10
```

MinGW:
```
cd edge_qt
cmake -B build -G "MinGW Makefiles" -DCMAKE_PREFIX_PATH="C:/Qt/6.7.0/mingw_64"
cmake --build build
build\edge_qt.exe --server http://SERVER_IP:8000 --lane 1 --targets 2 --shots 10
```
(Adjust the Qt install path/version and generator to match what you actually
installed -- check under `C:\Qt\` for the exact folder name.)

If Windows complains the .exe can't find Qt's DLLs when double-clicked
outside Qt Creator, run Qt's `windeployqt.exe` once against the built exe
(from a Qt command prompt): `windeployqt build\Release\edge_qt.exe` (MSVC) or
the equivalent path for MinGW -- this copies the needed Qt DLLs next to it.

## Command-line options

This app is always **one process = one lane** (there is no `--lanes` /
`--sequential` here -- see "Notes" below). To simulate several lanes,
launch it several times, once per `--lane`, exactly like edge.py's own
recommended deployment.

| Option | Default | Meaning |
|---|---|---|
| `--server <url>` | `http://127.0.0.1:8000` | Server base URL |
| `--lane <n>` | `1` | Lane number this edge instance simulates |
| `--targets <n>` | `2` | Number of physical targets on this lane |
| `--shots <n>` | `10` | Shots fired at each target before moving to the next |
| `--interval <sec>` | `0.5` | Delay between consecutive shots on the same target |
| `--target-interval <sec>` | `1.0` | Pause after a target's full string, before the next target |
| `--db <path>` | `edge<N>.db` next to the .exe | Override local sqlite db path |
| `--edge-code <code>` | `EDGE-LANE<N>` | Override edge_code |
| `--shooter-name <name>` | a Vietnamese name picked by lane | Shooter's name (single-shooter mode only) |
| `--shooter-code <code>` | `S<lane:02d>` | Override shooter_code (single-shooter mode only) |
| `--unit-code <code>` | `UNIT-LANE<lane>` | Override unit_code -- lets a shooter's unit be set independently of which lane they shoot on (single-shooter / `--people`, shared by all people). Ignored with `--roster`, where each entry has its own. |
| `--unit-name <name>` | `Lane <lane> Unit` | Override unit_name. Same scoping as `--unit-code`. |
| `--people <n>` | disabled | Evaluate N shooters sequentially on this ONE lane |
| `--people-names <a,b,c>` | Vietnamese name pool | Comma-separated names for `--people` |
| `--person-interval <sec>` | `2.0` | Pause between people/roster entries |
| `--roster <path>` | disabled | Preload the GUI's roster table (see below) from a CSV/JSON file; takes precedence over `--people` |
| `--no-autostart` | autostart ON | Wait for the Start button instead of running immediately |
| `--reset` | disabled | TESTING: delete this lane's local db + images folder, then exit immediately -- no window opens, every other option is ignored. See "Reset local data" below. |

Examples (two lanes = two separate processes, exactly like edge.py):
```
edge_qt --server http://SERVER_IP:8000 --lane 1 --targets 2 --shots 10
edge_qt --server http://SERVER_IP:8000 --lane 2 --targets 2 --shots 10
```
Four people taking turns on lane 1:
```
edge_qt --server http://SERVER_IP:8000 --lane 1 --people 4 --targets 2 --shots 10
```

## Roster (shooter list)

The window has a "Danh sách xạ thủ" (roster) table above the target image:
a list of shooters, each with their own name, unit name, unit code, and
(optional) shooter code. Add rows by hand with **Thêm** (Add), or click
**Tạo tự động** (Generate) to fill in N shooters round-robin across M
generated units (both counts are set with the two spinboxes next to the
button) -- e.g. 6 shooters / 2 units alternates unit 1, 2, 1, 2, 1, 2.
Clicking Generate more than once always ADDS a fresh batch on top of the
last one -- it never restarts the numbering, so every click mints new
shooter codes (`GEN-001`, `GEN-002`, ...) that never collide with an
earlier batch, and continues through the Vietnamese name pool instead of
reusing the same names from the start every time. If the table has ANY
rows when you click **Bắt đầu** (Start), the simulation
runs through them in order, one full session per row, attributing each
row's results to the unit/shooter named in that row -- this takes priority
over `--people`/single-shooter mode. Leave the table empty to use
`--people`/single-shooter mode as before. `--roster <path>` preloads the
table at startup from a CSV (header row: `name,unit_code,unit_name,
shooter_code` -- only `name` is required) or JSON (a list of objects with
the same keys) file; you can still edit the table before pressing Start.

This is also the fix for "shooters in one unit may be scheduled to shoot on
different lanes": a roster entry's unit always comes from the entry itself,
never implied by `--lane`, so the same real-world unit can show up correctly
across sessions run on different lanes/edges.

## Changing the lane number from the GUI

The **Lane:** spinbox at the top of the window (next to the title) lets you
change which lane this running instance simulates without closing it and
re-launching with a different `--lane` -- handy when repurposing an already-
open window for a different physical lane during testing. It starts at
whatever `--lane` resolved to, and any change immediately updates the window
title and the "Server / Lane / Targets / Shots" line above the roster; the
new lane number takes effect the next time you press **Bắt đầu** (Start),
exactly as if the app had been launched with that `--lane` value. Like the
roster buttons, it's only editable while nothing is running.

## Reset local data (testing)

To start a lane completely clean while testing, there are two equivalent
ways to wipe its local data (the sqlite db and rendered images folder next
to the executable) -- pick whichever suits the moment:

- **Command line**: `edge_qt --lane 1 --reset` deletes `edge1.db` and
  `images_lane1/` and exits immediately -- no window opens, every other
  option is ignored. Same idea as `edge.py --lane 1 --reset`.
- **In the GUI**: the **"Xóa dữ liệu"** (reset) button next to Start/Stop
  asks for confirmation, then deletes the same two things for the lane the
  window is currently configured for. Only enabled while nothing is
  running (same as the roster buttons).

Either way this only clears THIS edge's own local data -- it never touches
the server. To wipe the server's data too (every match/lane/shooter/shot,
across every edge), see `POST /api/admin/reset` in the top-level `README.md`.

## What it does

Same as `edge.py`: creates (idempotently, upserted on natural keys) this
lane's `unit` / `shooter` / `lane` / `physical_target` / `shooting_match`
rows in a **local** SQLite database (`edge<N>.db`, next to the executable),
opens a shooting session with one `session_target` per physical target,
renders and sends a blank "start" image for each target (before any shot,
so the viewer can show a before/after compare), fires each target's
**entire** string of shots in order (full blocks, not round-robin),
rendering a small target-face image with every hit recorded so far after
each shot, and pushes everything (topology, start image, shots, status) to
the server over `POST /api/sync` -- the exact same JSON payload shapes
`edge.py` sends, so `server.py` needs no changes at all and the web viewer
shows a lane run by this program exactly like one run by `edge.py`. If the
server is briefly unreachable, events simply stay queued in the local
`sync_queue` table and are retried on the next sync call (the next shot, or
session completion) -- nothing is lost, matching `edge.py`'s own
store-and-forward behavior.

The window shows: the live target image, current target/shot progress, the
last shot's score with its classification band (color-coded, same
Excellent/Good/OK/Fail thresholds `server.py`'s statistics use), a sync
status line, and a scrolling log of everything happening (session start,
each shot, each sync result) -- Start/Stop buttons let you control a run
manually, or leave it on autostart (default) to begin as soon as the window
opens, matching `edge.py`'s "just run it" style.

## Notes / simplifications vs `edge.py`

- No `--lanes` / `--sequential` (running several lanes orchestrated inside
  ONE process). This app is always one lane per process; launch it multiple
  times for multiple lanes -- which is how `edge.py` is meant to be deployed
  in production anyway (one process per physical lane device).
- Timestamps are ISO-8601 UTC with millisecond precision and a trailing `Z`
  (Qt's native format) instead of Python's microsecond `+00:00` form --
  both are valid, both sort correctly as plain text, and neither the server
  nor the viewer ever parses these columns as anything but display strings.
- Score rounding uses ordinary "round half away from zero" instead of
  Python's banker's rounding; since shots are randomly generated, this has
  no practical effect on results.
- The target-face image is drawn with Qt's QPainter instead of Pillow --
  same rings / crosshair / hit-color logic (green >=8, orange >=5, red
  otherwise), just not guaranteed to be pixel-identical to edge.py's PNGs.

## Project layout

```
edge_qt/
  CMakeLists.txt       - build configuration (Qt6: Widgets, Network, Sql)
  resources.qrc        - UNUSED, safe to ignore/delete (see note below)
  README_QT.md         - this file
  src/
    main.cpp           - CLI parsing (incl. --roster file loading), launches the GUI
    EdgeConfig.h        - all the --flags in one struct, plus the RosterEntry
                          struct ({name, unitCode, unitName, shooterCode})
    NamePool.h          - the same Vietnamese shooter-name pool as edge.py
    VnLabels.h          - Vietnamese UI strings (result bands, buttons, roster table, ...)
    EdgeDatabase.*      - local SQLite access; a line-by-line C++ port of
                          edge.py's seed()/_start_session()/_fire_target_block()/
                          _complete_session()/sync() database logic (unit/
                          shooter identity decoupled from the lane number,
                          start-image rendering); also locates and loads the
                          shared ../schema.sql (see findSchemaPath()) and
                          applies the same additive-column migrations
                          server.py/edge.py do
    TargetRenderer.*    - draws the target-face + hits image (QPainter)
    SyncClient.*        - blocking POST /api/sync (QNetworkAccessManager)
    EdgeWorker.*        - background QThread that runs the whole simulation
                          (single-shooter / --people / roster) so the GUI
                          stays responsive, and emits Qt signals for the
                          window to display
    MainWindow.ui       - the window's widget layout (Qt Designer form),
                          compiled by AUTOUIC into ui_MainWindow.h; every
                          plain-ASCII label, size and range lives here
    MainWindow.*        - the GUI's behavior: aliases its member pointers
                          onto the widgets ui_MainWindow.h declares, fills
                          in Vietnamese text via VnLabels (kept out of the
                          .ui file -- see note below), and implements the
                          roster table (add/remove/generate rows), the lane
                          spinbox, and the "Xóa dữ liệu" reset button
                          (deletes this lane's local db + images)
```

**Note on `MainWindow.ui` and Vietnamese text:** like every other string in
this project (see `VnLabels.h`), UI text that isn't plain ASCII is never
written directly into source that a tool other than the C++ compiler parses
-- `uic` (which compiles `.ui` files) is no exception. So `MainWindow.ui`
only carries widget structure, sizes, ranges, and pure-ASCII strings (the
window title, "Lane:", the roster hint's CSS); every Vietnamese label, button
and column heading is left blank in the form and set in C++ by
`MainWindow::buildUi()` via the same `\xHH`-escaped `VnLabels::xxx()` calls
used everywhere else, right after `ui->setupUi(this)` runs. If you edit the
form in Qt Designer, leave those text properties empty rather than typing
Vietnamese into them.

**Note on `resources.qrc`:** an earlier version of this project embedded
`../schema.sql` into the binary via this Qt resource file. That tripped a
CMake/AUTORCC bug where `rcc`'s dependency scanner fails on a `.qrc` entry
that points outside its own directory ("The rcc list process failed"), so
`schema.sql` is now loaded directly off disk at runtime instead (see
`EdgeDatabase::findSchemaPath()` — it checks next to the `.exe` and a few
folders above it, with a build-time absolute path as a last-resort
fallback). `resources.qrc` is no longer referenced by `CMakeLists.txt` and
can be deleted; it's harmless to leave in place too.
