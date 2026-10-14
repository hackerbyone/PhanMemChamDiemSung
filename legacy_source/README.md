# Real Server + Web/Mobile Viewer

Đây là bản nâng cấp của prototype:
- Edge vẫn là simulator (SQLite riêng cho từng lane, không đổi).
- Server là FastAPI + **SQLite** (1 file `server.db` duy nhất), có REST API + WebSocket.
- Viewer là Web responsive, dùng được trên PC/tablet/phone trong LAN, có thêm
  **chế độ Quản lý** (đăng nhập bằng mật khẩu) xem thống kê.
- Quản lý Match -> Lane -> Shooter -> Session -> Target -> Shot.
- Dashboard realtime, số xạ thủ đã bắn, và thống kê phân loại kết quả.
- Có 2 lựa chọn cho Edge: `edge/edge.py` (Python, dùng như trước) hoặc
  `edge_qt/` (Qt/C++, có giao diện đồ hoạ hiển thị ảnh bia trực tiếp) — cả
  hai nói cùng một giao thức với server nên dùng cái nào cũng được, kể cả
  trộn lẫn (lane này chạy edge.py, lane khác chạy edge_qt) trên cùng một
  server. Xem `edge_qt/README_QT.md` để build/chạy bản Qt.

## 1. Cài đặt Python packages

Không cần cài thêm database server nào — SQLite có sẵn trong Python. Chỉ
cần cài các package Python:

    pip install -r requirements.txt

(nếu máy có nhiều bản Python, dùng đúng interpreter sẽ chạy server:
`python -m pip install -r requirements.txt`)

## 2. Chạy Server

    cd server
    python -m uvicorn server:app --host 0.0.0.0 --port 8000

Server tự tạo file `server/server.db` và toàn bộ bảng bên trong khi khởi
động lần đầu (đọc schema từ `schema.sql`) — không cần cài đặt gì thêm. Muốn
làm lại từ đầu (xoá hết dữ liệu) thì tắt server rồi xoá file `server.db`,
chạy lại server sẽ tự tạo bảng mới.

Biến môi trường tuỳ chọn:

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `MANAGER_PASSWORD` | `admin123` | Mật khẩu đăng nhập chế độ Quản lý trên Viewer — **nên đổi** |

Ví dụ chạy trên Windows PowerShell với mật khẩu quản lý riêng:

    cd server
    $env:MANAGER_PASSWORD="mat-khau-quan-ly-cua-ban"
    python -m uvicorn server:app --host 0.0.0.0 --port 8000

API docs:
http://SERVER_IP:8000/docs

## 3. Viewer

Cách đơn giản:
cd viewer
python -m http.server 8080 --bind 0.0.0.0

PC:
http://127.0.0.1:8080

Phone trong LAN:
http://SERVER_IP:8080

Viewer tự kết nối tới server cùng host nếu chạy qua HTTP. Nếu mở file:// thì nó dùng 127.0.0.1.

## 4. Edge simulator

Mỗi tiến trình `edge.py` mô phỏng ĐÚNG MỘT lane. Chạy 2 lane song song = chạy
2 tiến trình (2 terminal), mỗi cái với `--lane` khác nhau:

    cd edge
    # Terminal 1
    python edge.py --server http://SERVER_IP:8000 --lane 1 --targets 2 --shots 10
    # Terminal 2
    python edge.py --server http://SERVER_IP:8000 --lane 2 --targets 2 --shots 10

Mỗi edge là một "thiết bị" độc lập: db riêng (`edge_lane<N>.db`), thư mục ảnh
riêng (`images_lane<N>/`), edge_uuid/unit/shooter/match riêng — không đụng
nhau, vì `lane.lane_number` là duy nhất toàn hệ thống nên KHÔNG được chạy 2
edge cùng `--lane`. edge.py tự tạo unit/shooter/lane/target/match cho lane đó
rồi đồng bộ toàn bộ topology lên server qua /api/sync (server tự đăng ký edge
và dựng lại đúng cấu trúc từ các sự kiện CREATE, không cần seed tay phía
server). Mỗi phát bắn được vẽ thành 1 ảnh bia (vòng điểm + toạ độ các phát bắn
đã có), lưu local rồi gửi kèm (base64) lên server; server lưu vào
server/images/ và trả về qua /images/<file>.png để Viewer hiển thị.
Trạng thái mỗi lane/target (WAITING → ACTIVE "đang bắn" → COMPLETED "xong")
cũng được đồng bộ realtime lên server và hiển thị trên Viewer dưới dạng badge.

Viewer không quản lý Match nữa (không Start/Complete/New Match) — nó luôn
hiển thị session MỚI NHẤT của từng lane, bất kể lane đó thuộc match nào hay
edge nào tạo ra nó, nên cứ chạy edge là lane đó tự xuất hiện/tự cập nhật trên
Viewer. Thay vì hiển thị mã match (`MATCH-L1-...`), Viewer hiển thị TÊN ĐƠN VỊ
của xạ thủ đang bắn trên lane đó. Chọn lane để xem chi tiết (ảnh bia + điểm
từng target + tổng điểm lane), phía trên góc phải hiển thị HAI số riêng biệt:
tổng số xạ thủ đã bắn (toàn bộ các lane, tính từ trước tới nay — cùng cách
tính với thống kê ở chế độ Quản lý, mục 5, nên hai nơi luôn khớp nhau) và số
xạ thủ đã bắn riêng trên lane đang chọn; phía dưới là bảng tổng hợp tất cả
lane. Bấm vào ảnh bia của một target sẽ mở bảng so sánh ảnh lúc bắt đầu (chưa
bắn) với ảnh hiện tại/kết thúc và danh sách từng phát bắn — xem mục 5.1.

Vì xạ thủ trong CÙNG một đơn vị có thể được xếp bắn ở NHIỀU lane khác nhau
(đơn vị không còn gắn cứng với lane nữa — xem `--unit-code`/`--unit-name`
bên dưới và mục "Roster" 4.2), số liệu theo đơn vị trong thống kê Quản lý
cộng dồn đúng trên mọi lane mà đơn vị đó có xạ thủ tham gia.

### 4.1 Đánh giá điểm bắn của N người (sequence nhiều người trên 1 lane)

Để mô phỏng N người lần lượt bắn trên CÙNG một lane (mỗi người bắn đủ 1 lượt
rồi đến người tiếp theo), dùng `--people N` thay vì chạy nhiều lane:

    cd edge
    python edge.py --server http://SERVER_IP:8000 --lane 1 --people 4 --targets 2 --shots 10
    # tuỳ chọn: đặt tên riêng cho từng người
    python edge.py --server http://SERVER_IP:8000 --lane 1 --people 4 \
        --people-names "Nguyễn Văn A,Trần Thị B,Lê Văn C,Phạm Thị D" --targets 2 --shots 10

Mỗi người được cấp một mã xạ thủ riêng (`S<lane>-<số thứ tự>`) nên kết quả
của từng người vẫn tách biệt rõ ràng trong lịch sử (dropdown "Lịch sử" trên
Viewer) và trong thống kê Quản lý (đếm đúng số xạ thủ/đơn vị). `--people`
không dùng chung với `--lanes` (đó là mô phỏng nhiều LANE cùng lúc, còn
`--people` là nhiều NGƯỜI trên một lane).

### 4.2 Đơn vị độc lập với lane, và danh sách xạ thủ nhiều đơn vị (`--roster`)

Mặc định, đơn vị (`unit`) của một lane được đặt tên tự động theo lane
(`UNIT-LANE<n>` / "Lane N Unit"), như trước giờ. Nhưng xạ thủ trong CÙNG một
đơn vị thật ngoài đời có thể được xếp bắn ở NHIỀU lane khác nhau, nên có thể
đặt tên đơn vị độc lập với lane bằng `--unit-code`/`--unit-name`:

    python edge.py --server http://SERVER_IP:8000 --lane 1 \
        --unit-code "TDBB1" --unit-name "Tiểu đoàn Bộ binh 1" \
        --shooter-code "S01" --shooter-name "Nguyễn Văn A"

Để đánh giá một DANH SÁCH xạ thủ có tên+đơn vị cụ thể (mỗi người một đơn vị
khác nhau, khác với `--people` là tất cả dùng chung 1 đơn vị theo lane), dùng
`--roster <file>` với file CSV hoặc JSON:

    # roster.csv
    name,unit_code,unit_name,shooter_code
    Nguyễn Văn A,TDBB1,Tiểu đoàn Bộ binh 1,A01
    Trần Thị B,TDBB2,Tiểu đoàn Bộ binh 2,B01

    python edge.py --server http://SERVER_IP:8000 --lane 1 --roster roster.csv --targets 2 --shots 10

(JSON tương đương: một mảng object cùng 4 khoá `name`/`unit_code`/
`unit_name`/`shooter_code`, chỉ `name` là bắt buộc.) Mỗi dòng trong roster
chạy đủ 1 lượt bắn trên CÙNG lane đó, theo đúng thứ tự trong file, rồi mới
đến dòng kế tiếp — giống `--people` nhưng mỗi người mang đơn vị riêng của
mình thay vì dùng chung đơn vị của lane. `edge_qt` có tính năng tương đương
ngay trong giao diện (bảng "Danh sách xạ thủ" — thêm dòng bằng tay, hoặc bấm
"Tạo tự động" để sinh N xạ thủ rải đều qua M đơn vị) — xem
`edge_qt/README_QT.md`.

## 5. Chế độ Quản lý & Thống kê (trên Viewer)

Nút "🔒 Quản lý" ở góc phải header — nhập mật khẩu (mặc định `admin123`,
đổi bằng biến môi trường `MANAGER_PASSWORD` khi chạy server, xem mục 2) để
đăng nhập. Sau khi đăng nhập, nút "📊 Thống kê" hiện bảng thống kê:
- Phân loại kết quả theo từng phát bắn: Xuất sắc (≥9), Tốt (7–8.9), Đạt
  (5–6.9), Không đạt (<5).
- Kết quả theo từng lane / từng bia trong lane đó.
- Kết quả theo từng đơn vị (số xạ thủ, số lượt bắn...).
- Các số liệu chung: tổng phát bắn, tổng lượt bắn, số xạ thủ đã bắn, số đơn
  vị, số lane. (Không hiển thị điểm cao nhất/thấp nhất/trung bình — phần
  phân loại theo băng điểm ở trên đã đủ để đánh giá chất lượng bắn.)

Đăng nhập quản lý dùng chung 1 mật khẩu (không có tài khoản riêng từng
người) — phù hợp cho demo/thao trường nội bộ. Token đăng nhập lưu trong
trình duyệt (localStorage) và trên server (bộ nhớ, mất khi restart server).

### 5.1 So sánh ảnh & sửa kết quả (chế độ Quản lý)

Bấm vào ảnh bia của một target (ở lane đang xem chi tiết) mở bảng so sánh:
ảnh lúc BẮT ĐẦU (chưa bắn phát nào) bên trái, ảnh HIỆN TẠI/KẾT THÚC (đã cộng
dồn hết các phát bắn) bên phải, cùng danh sách từng phát bắn kèm điểm số —
để xạ thủ tự kiểm tra lại xem hệ thống có chấm nhầm phát nào không. Nếu là
Quản lý đã đăng nhập, có thể bấm chọn 1 dòng phát bắn trong danh sách rồi
bấm vào vị trí đúng trên ảnh bên phải để SỬA cả điểm số VÀ toạ độ trúng đích
của phát bắn đó (không chỉ sửa điểm) — hệ thống tự tính lại điểm theo
khoảng cách mới, cập nhật lại tổng điểm bia/lượt bắn, và đánh dấu phát bắn
đó là "Đã sửa". Việc sửa không vẽ lại ảnh đã lưu (vì sẽ phải vẽ lại toàn bộ
chuỗi ảnh cộng dồn sau đó) — chỉ số liệu (điểm, toạ độ) được cập nhật, ảnh
gốc giữ nguyên làm bằng chứng, badge "Đã sửa" cho biết số liệu đã được
Quản lý điều chỉnh.

## 6. Luồng thật

Edge:
Camera -> Detection -> Score -> SQLite (local, từng lane) -> SyncQueue
                                                          |
                                                          v
                                                       REST /sync
                                                          |
                                                          v
Server:
SQLite (server.db) -> Dashboard/API/Thống kê -> WebSocket -> Viewer

Không truyền video camera lên Server.

## 7. Production

Viewer nên chuyển thành React/React Native/PWA.
Nên có tài khoản Quản lý riêng từng người (không dùng chung 1 mật khẩu),
audit log, HTTPS nếu mạng không hoàn toàn tin cậy, và backup định kỳ cho
file `server.db` (SQLite phù hợp cho demo/thao trường nội bộ quy mô vừa;
nếu cần nhiều tiến trình ghi đồng thời ở quy mô lớn hơn thì cân nhắc
PostgreSQL trở lại — server.py trước đây đã hỗ trợ việc này).

## 8. Xóa toàn bộ dữ liệu để test (reset)

Khi đang test và muốn bắt đầu lại từ đầu (không còn match/lane/xạ thủ/phát
bắn cũ nào), có 2 chỗ cần xoá riêng biệt — Server và từng Edge — vì mỗi bên
giữ dữ liệu của mình trong SQLite riêng, độc lập với nhau.

### 8.1 Xoá dữ liệu Server

`POST /api/admin/reset` — xoá **toàn bộ** dữ liệu trong `server.db` (mọi
bảng: match, lane, xạ thủ, đơn vị, session, target, phát bắn, hàng đợi
đồng bộ, log sự kiện...) về trạng thái rỗng như mới cài, và xoá luôn mọi
ảnh bia đã lưu trong thư mục `images/`. **Không thể hoàn tác.**

Yêu cầu: đăng nhập Quản lý (`X-Manager-Token`, xem mục 5) **và** phải gửi
đúng cụm xác nhận `"DELETE ALL"` trong body — để tránh việc gọi nhầm (ví dụ
một request đã lưu sẵn, hay copy-paste nhầm lệnh curl) xoá sạch dữ liệu
thật ngoài ý muốn. Endpoint này **không** có nút bấm tương ứng trên Viewer —
chỉ gọi thủ công lúc test, ví dụ bằng curl:

    # 1) đăng nhập lấy token quản lý
    curl -s -X POST http://SERVER_IP:8000/api/manager/login \
      -H "Content-Type: application/json" -d '{"password":"admin123"}'
    # -> {"ok":true,"token":"..."}

    # 2) gọi reset với token đó
    curl -s -X POST http://SERVER_IP:8000/api/admin/reset \
      -H "Content-Type: application/json" -H "X-Manager-Token: TOKEN_LẤY_Ở_TRÊN" \
      -d '{"confirm":"DELETE ALL"}'

Hoặc dùng trang Swagger tại `http://SERVER_IP:8000/docs` (nút "Authorize"
không áp dụng cho header tuỳ biến này, nên vẫn cần điền `X-Manager-Token`
thủ công trong phần "Try it out" của từng endpoint).

Response trả về số dòng đã xoá theo từng bảng và số ảnh đã xoá, ví dụ:
`{"ok":true,"deleted_rows":{"shot":42,...},"deleted_images":37}`.

### 8.2 Xoá dữ liệu Edge

Mỗi edge (Python hoặc Qt) giữ dữ liệu cục bộ của LANE đó trong file
`edge<N>.db` + thư mục ảnh `images_lane<N>/` nằm cạnh chương trình — xoá
2 thứ này là "reset sạch" một lane, độc lập với Server (không cần Server
đang chạy) và độc lập với các lane khác.

**edge.py:**

    python edge.py --lane 1 --reset
    # xoá edge1.db + images_lane1/ rồi thoát ngay, không mô phỏng gì cả

Cũng dùng được với `--lanes` để xoá nhiều lane một lượt:

    python edge.py --lanes 1,2,3 --reset

**edge_qt:** hai cách tương đương —
- Dòng lệnh: `edge_qt --lane 1 --reset` (thoát ngay, không mở cửa sổ),
  giống hệt edge.py.
- Trong giao diện: nút **"Xóa dữ liệu"** cạnh nút Bắt đầu/Dừng — bấm sẽ hỏi
  xác nhận trước khi xoá `edge<N>.db` + `images_lane<N>/` của lane đang mở
  (chỉ bấm được khi không đang chạy mô phỏng).

Lưu ý: xoá dữ liệu Edge và Server là 2 việc tách biệt — xoá Edge không xoá
gì trên Server (dữ liệu cũ của lane đó vẫn còn trên Server cho tới khi bạn
cũng gọi reset ở mục 8.1), và ngược lại.
