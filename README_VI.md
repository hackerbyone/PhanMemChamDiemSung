# Chấm điểm tự động từ hai ảnh — v2

## Bổ sung quản lý và tra cứu

Giữ nguyên thuật toán nhận diện/chấm điểm. Bản này bổ sung đủ 5 nhóm chức năng:

1. **Lịch sử & báo cáo**: tìm mã, tên hoặc đơn vị; lọc khoảng ngày, bệ bắn và trạng thái. Nhấp đúp một loạt để mở lại ảnh và kết quả trong tab Loạt bắn. Các thao tác sửa/bỏ/thêm lỗ hoặc xác nhận kiểm tra tiếp tục lưu phiên bản mới của chính mã loạt đó, giữ ảnh và JSON cũ. Nếu loạt đã được sửa bởi một cửa sổ khác, phần mềm yêu cầu mở lại trước khi lưu tiếp.
2. **Khôi phục loạt dang dở**: khi khởi động, ứng dụng đề nghị mở danh sách ảnh nháp chưa có kết quả. Có thể mở bằng nút Khôi phục loạt dang dở ở tab Lịch sử & báo cáo. Chọn nháp, bấm Khôi phục; nếu đã có ảnh sau thì bấm Tính lại / thử lại. Nếu mới có ảnh trước thì chụp ảnh sau như bình thường. Nút Ẩn nháp chỉ ẩn khỏi danh sách, vẫn giữ ảnh trên đĩa.
3. **Xuất Excel và in phiếu**: Xuất Excel theo bộ lọc tạo `.xlsx` gồm Loạt bắn, Theo người, Theo đơn vị và Chi tiết lỗ. Tổng hợp DEMO tách khỏi thực tế; các tổng thực tế có bao gồm kết quả chưa duyệt, thể hiện rõ trong báo cáo. Chọn một hoặc nhiều loạt bằng Ctrl/Shift, bấm In phiếu đã chọn để tạo HTML chứa ảnh chú thích và mở trình duyệt. Bấm In phiếu / Lưu PDF rồi chọn máy in hoặc Save as PDF. Phiếu có trạng thái kiểm tra, mã loạt, phiên bản và chỗ ký. Không cần cài Excel để xuất tệp.
4. **Danh sách người bắn**: thêm, sửa, tìm hoặc nhập danh sách CSV UTF-8; bấm Tải CSV mẫu để lấy mẫu. CSV cần các cột `code,name,unit` hoặc `Mã người bắn,Họ tên,Đơn vị`. Chọn người rồi bấm Chọn để bắn để tự điền thông tin. Mã duy nhất; nhập CSV có mã trùng nhưng khác thông tin sẽ báo lỗi và không nhập một phần. Sửa danh sách không đổi thông tin lịch sử. Người nhập tay được bổ sung vào danh sách sau khi lưu kết quả lần đầu nếu mã chưa tồn tại; DEMO không được thêm.
5. **Kiểm tra ảnh phóng to**: mở bằng nút ở tab Loạt bắn hoặc nhấp đúp dòng lỗ. Chọn ảnh trước/sau nguyên gốc, ảnh sau đã căn hoặc bia mẫu; bật/tắt vòng điểm và tâm lỗ. Cuộn chuột để phóng to, kéo chuột để di chuyển; chọn dòng hoặc nhấp lỗ để làm nổi vị trí tương ứng. Màn hình thêm/sửa tâm và căn chỉnh thủ công cũng hỗ trợ phóng to, kéo ảnh.

Form loạt bắn có **Ngày bắn (YYYY-MM-DD)** và **Ghi chú**. Ngày mặc định theo múi giờ Việt Nam; thời điểm lưu/chụp vẫn giữ ISO UTC riêng. **Bệ bắn** sử dụng trường `lane` cũ nên vẫn tương thích đồng bộ. **Loạt tiếp theo** giữ người bắn và tăng số loạt; **Người tiếp theo** xóa thông tin người bắn và đặt loạt về 1.

### Cơ sở dữ liệu cục bộ

`data/rounds.db` có hai bảng chính:

| Bảng | Trường |
|---|---|
| `shooters` | `code`: mã người bắn (khóa chính); `name`: họ tên; `unit`: đơn vị; `updated_at`: thời điểm cập nhật |
| `rounds` | `id`: mã loạt UUID (khóa chính); `shooter_code`, `shooter`, `unit`, `shooting_date`: ngày bắn; `series`: loạt; `lane`: bệ; `expected_shots`: số phát dự kiến; `total_holes`: số lỗ tính; `total_score`: tổng điểm; `status`: trạng thái; `notes`: ghi chú; `synced`: đã đồng bộ; `payload`: kết quả đầy đủ |

Thông tin người/đơn vị trong `rounds` là bản chụp tại thời điểm bắn, không tự thay đổi theo danh sách người bắn. `payload` lưu thêm ghi chú, giờ chụp, cấu hình bia, tọa độ lỗ, cảnh báo và phiên bản. Ứng dụng tự thêm cột và chỉ mục cho cơ sở dữ liệu v2 cũ; không xóa ảnh, sửa payload cũ hoặc đặt lại trạng thái đồng bộ. Với bản ghi chưa có ngày bắn, ngày tra cứu suy ra từ giờ nhận ảnh trước, hoặc giờ tạo kết quả, quy đổi sang giờ Việt Nam.

Ảnh nháp mới giữ cả cấu hình và hash mẫu để khôi phục đúng hình học. Khi mở lại loạt có hash ảnh mẫu khác ảnh hiện tại, ứng dụng yêu cầu khôi phục đúng ảnh mẫu trước khi sửa. Nháp cũ chưa lưu cấu hình dùng cấu hình mẫu hiện tại. Đây là quản lý cục bộ; danh sách người bắn không tự đồng bộ thành danh bạ chung trên server.

Kiểm thử giao diện tùy chọn trên Windows có Tcl/Tk: đặt `RUN_DESKTOP_TESTS=1` rồi chạy bộ unittest như cuối tài liệu. Kiểm thử dùng thư mục tạm, không mở camera hay ghi dữ liệu thật.

## Luồng chấm điểm

Bản này thay luồng nhiều thao tác của v1 bằng:

**Chọn người bắn → chụp trước → chụp sau → tự xem và lưu kết quả.**

Máy Windows 64-bit, CPU, camera USB. Đã kèm model `best.pt` và hai ảnh bạn cung cấp.

## Chạy nhanh

1. Giải nén **toàn bộ** thư mục `shooting_scoring_auto_v2`, không chạy trong ZIP.
2. Chạy `setup_windows.bat` một lần, có Internet. Nếu máy đã có Python Install Manager, script thử cài Python 3.12 khi thiếu. Nếu không có `py`, cài Python 3.12 64-bit từ python.org, gồm Launcher và Tcl/Tk, rồi chạy lại script.
3. Chạy `start_edge.bat` để chụp ảnh và chấm điểm tại máy. **Không cần khởi động Server nếu chỉ dùng cục bộ.**
4. Lần đầu mở, ứng dụng tự tìm đường bao hình người, tâm và các vòng. Xem hình có đường bao xanh/vòng vàng rồi xác nhận **một lần**. Không phải bấm từng điểm. Những lần sau dùng lại cấu hình đã lưu.
5. Có thể bấm **Thử hai ảnh đã gửi** để chạy toàn bộ luồng ngay. Các bản ghi này được đánh dấu **DEMO**, không phải kết quả thực tế.

Nếu vẫn lỗi cài Python, mở CMD chạy `py install 3.12`, rồi chạy lại setup. Nếu launcher cũ không hiểu lệnh `install`, dùng trình cài Python 3.12 64-bit. Giữ cửa sổ lỗi hoặc chụp thông báo nếu có lỗi thư viện.

## Sử dụng thực tế

Điền mã người bắn, họ tên, đơn vị, làn, số loạt; số phát dự kiến là tùy chọn. Bấm **Mở / đóng camera**, mặc định camera 0.

- **1. Chụp TRƯỚC**: lưu trạng thái bia trước loạt ngay vào đĩa, khóa thông tin người/loạt để tránh đổi nhầm.
- **2. Chụp SAU & tính điểm**: lưu ảnh sau ngay; tự căn chỉnh, nhận diện, đối chiếu, tính điểm và lưu kết quả. Không có nút Phân tích hoặc Lưu bắt buộc nữa.
- **Loạt tiếp theo**: giữ người bắn, tăng số loạt và chụp ảnh trước mới. **Người tiếp theo**: bắt đầu loạt 1 với người khác; có thể chọn từ danh sách.

Nếu có sẵn ảnh, dùng Mở ảnh trước rồi Mở ảnh sau & tính điểm. Mở ảnh sau cũng tự chạy. Nhập ảnh chỉ lưu thời gian nhập; không giả định đó là thời điểm chụp gốc. Ảnh đầu phải thuộc chính tờ bia đang dùng, không thay bằng ảnh mẫu khi chấm thật.

Bật camera giữ hình trực tiếp ở khung bên trái; ảnh trước đã chụp vẫn được lưu riêng. Nút camera lần nữa sẽ đóng camera. Nếu sai camera, đổi chỉ số 0/1/2 ở tab **Thiết lập & kiểm tra**.

## Luồng xử lý đúng theo yêu cầu

1. **Phân tích ảnh mẫu:** tách vùng tối, nối nét in; lấy đường bao chính và lấp đầy theo từng hàng để giữ cả phần mặt/tay/súng sáng bên trong hình người. Cách lấp theo hàng được thiết kế riêng cho hình dáng bia số 4 này, không phải bộ tách mọi loại bia.
2. **Tìm vòng điểm:** tìm các đường tròn có tâm gần nhau bằng Hough, tìm đỉnh tương phản theo bán kính, rồi khớp chung tâm và 5 bán kính từ các cung thực sự thấy được. Không đọc tọa độ tâm/bán kính từ một danh sách gán sẵn và không bắt buộc các vòng cách đều. Vòng 6 khuyết vẫn được khớp từ các cung còn lại. Mẫu mới phải gần chính diện, đủ rõ và có đúng hệ vòng này; mẫu không đạt bị từ chối.
3. **Căn ảnh:** SIFT + RANSAC tính homography riêng cho ảnh trước và ảnh sau. Biến đổi **toàn ảnh** về kích thước và tọa độ ảnh mẫu. Kiểm tra inlier, sai số, độ phủ và hình người có được chụp đủ không; không đạt thì không tự sinh điểm.
4. **YOLO:** chạy model trên hai ảnh **đã căn chỉnh**. Tâm khung nhận diện dùng làm tâm lỗ; tọa độ lúc này đã ở khung mẫu. Lưu cả phép biến đổi ngược để tra vị trí trên ảnh gốc.
5. **Đối chiếu:** ghép một-một các lỗ trước/sau theo khoảng cách. Lỗ cũ không cộng lại. Lỗ chỉ có ở ảnh sau là ứng viên mới. Không chỉ lấy số lỗ sau trừ số lỗ trước.
6. **Tính điểm:** ngoài đường bao hình người = 0; trong hình người = 5–10 tùy vòng; ngoài vòng 6 nhưng còn trong hình người = 5. Không dùng đường tròn vòng 5 để loại vùng trong hình người. Trên vạch vòng lấy điểm cao hơn; xét tâm, không xét mép lỗ. Tọa độ là pixel mẫu, chưa giả định mm.

## Khi nào cần kiểm tra?

Kết quả luôn có trạng thái rõ:

| Trạng thái | Ý nghĩa |
|---|---|
| AUTO | Kết quả tự động, chưa có người xác nhận; không có cảnh báo theo các ngưỡng hiện tại |
| NEEDS_REVIEW | Có cảnh báo hoặc vừa sửa kết quả |
| REVIEWED | Người vận hành đã xác nhận kiểm tra |
| DEMO | Thử ảnh mẫu; không dùng như kết quả bắn thực tế |

Cảnh báo gồm nhận diện yếu, lỗ sát mép ảnh, gần đường phân điểm/đường bao, gần lỗ cũ, lỗ cũ không thấy lại, hoặc số lỗ mới khác số phát dự kiến. Ngưỡng mặc định chỉ là khởi điểm, không phải chứng nhận độ chính xác.

Tab **Thiết lập & kiểm tra** chứa các thao tác hiếm dùng: xem ảnh chồng, chọn homography thủ công nếu tự động thất bại, thêm/sửa tâm, bỏ/tính lỗ, xác nhận đã kiểm tra. Chọn dòng ở tab Loạt bắn rồi vào tab này để sửa. Nhấn Escape trong bảng để bỏ chọn khi muốn thêm lỗ mới. Mỗi lần sửa tự lưu một phiên bản mới; server giữ lịch sử phiên bản cũ, không tạo một loạt thứ hai.

Nếu mẫu tự tìm chưa khớp, **không xác nhận**. Có thể thay bằng ảnh mẫu bia số 4 rõ, chính diện tại `assets/Biaso4.png`, khởi động lại và nhận dạng lại. Bản này không yêu cầu người dùng vẽ từng nét; chưa có màn hình sửa hình học bia mẫu bằng tay. Homography chỉ phù hợp với bia gần phẳng; giấy cong/nhăn hoặc méo ống kính vẫn có thể gây sai lệch cục bộ dù sai số khớp điểm nhỏ.

Hai ảnh không thể xác định chắc chắn thứ tự từng phát hay số phát trúng chồng hoàn toàn. Tâm bounding box cũng có thể lệch tâm lỗ rách thật. Cần đánh giá với nhãn chuẩn trước khi sử dụng điểm như kết quả chính thức.

## Đồng bộ và xem web — tùy chọn

1. Chạy `start_server.bat`, nhập mật khẩu quản lý, giữ cửa sổ mở.
2. Trong tab Thiết lập, nhập địa chỉ server (cùng máy: `http://127.0.0.1:8000`) và đúng mật khẩu. Không lưu mật khẩu xuống tệp.
3. Mặc định ứng dụng tự gửi sau khi lưu nếu đã nhập mật khẩu. Nếu mất mạng, bản ghi vẫn nằm trong hàng đợi; bấm **Gửi lại các loạt đang chờ** sau khi mạng trở lại. Chưa có lịch gửi lại định kỳ.
4. Mở **http://127.0.0.1:8000/real** để xem ảnh trước/sau, kết quả, trạng thái. Trang tự làm mới 5 giây/lần và hiển thị 200 loạt gần nhất.

Bản v2 cập nhật bảng `real_rounds` và thêm `real_round_history` cho các phiên bản sửa. Kết quả thật **vẫn xem riêng** với hệ thống thống kê mô phỏng cũ; chưa gộp vào các bảng `shooting_session/shot` hoặc giao diện Qt. Không dùng API reset mô phỏng cho dữ liệu thật.

## Tệp dữ liệu

- `target_config.json`: cấu hình tự tìm, hash ảnh mẫu, chất lượng khớp vòng, xác nhận lần đầu; thay ảnh mẫu làm hết hiệu lực cache.
- `data/drafts/<UUID>/before.png`, `after.png`, `metadata.json`: ảnh chụp được lưu ngay. Dùng danh sách Khôi phục loạt dang dở để tiếp tục cùng mã loạt và thông tin đã lưu.
- `data/<UUID>/rev_N/`: hai ảnh nguyên hình không ghi đè chú thích, ảnh chú thích riêng và kết quả JSON của từng phiên bản.
- `data/<UUID>/result.json`: bản mới nhất; `data/rounds.db`: kết quả và trạng thái đồng bộ.
- `server/server.db`, `server/images/real/`: bản nhận trên server; ảnh các phiên bản được giữ riêng.

Ảnh camera lưu PNG; tệp ảnh nhập được giải mã và mã hóa lại PNG, không phải bản sao byte y hệt tệp gốc. Sao lưu toàn bộ `data` và dữ liệu server cùng nhau.

## Nâng cấp từ v1

Giải nén v2 thành thư mục mới, giữ nguyên v1. Chạy setup riêng để tạo môi trường cho v2. Đóng cả Edge/Server trước khi chuyển dữ liệu. Nếu cần mang dữ liệu cũ, sao chép nguyên `data` và `server/server.db` cùng `server/images` từ v1 sang vị trí tương ứng ở v2; giữ bản sao dự phòng. V2 đọc được cấu trúc lưu cũ và cấu trúc mới, không tự di chuyển tệp trên máy của bạn. Không sao chép `target_config.json` hoặc `.venv` cũ. Chỉ chạy một server tại cổng 8000.

## Kiểm thử trong môi trường phát triển

- Chạy model `best.pt` trên CPU với đúng luồng ảnh đã căn chỉnh: ảnh mẫu 0 phát hiện, ảnh sau 8 ứng viên trong lần kiểm tra này. Đây **không phải số phát hoặc điểm đã kiểm chứng**, vì không có nhãn chuẩn và ảnh trước thực tế.
- Tự tìm được tâm gần (450.82, 452.72); bán kính khoảng 98.60 / 198.66 / 298.86 / 398.85 / 497.90 px; sai số trung vị trên các cung khớp khoảng 0.61–1.10 px. Đây là kết quả khớp trên ảnh mẫu, không phải sai số chấm điểm thực tế.
- Kiểm thử vùng điểm và đường bao, homography và ảnh thiếu vùng bia, đảm bảo YOLO nhận ảnh đã warp, ghép lỗ cũ/mới, lưu các bản sửa và gửi lại chống trùng, xác thực API và từ chối sai điểm.
- Chưa kiểm thử camera USB vật lý hoặc giao diện/cài đặt trên máy Windows của bạn.

`check_samples.bat` tạo `sample_check/automatic_geometry.png`, `automatic_result_demo.png`, `alignment_overlay.png` và `automatic_pipeline.json`. Đây là chẩn đoán DEMO, không đồng bộ.

Chạy kiểm thử: `.venv\Scripts\python -m pip install httpx`, rồi `.venv\Scripts\python -m unittest discover -s tests -v`.

Các thư viện được ghim trong `requirements.txt`; PyTorch dùng bản CPU. Tài liệu API tham khảo:
- https://docs.opencv.org/4.x/d4/d70/tutorial_hough_circle.html
- https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html
- https://docs.ultralytics.com/modes/predict/
