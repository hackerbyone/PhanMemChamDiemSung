#pragma once
#include <QString>

// Small set of Vietnamese UI strings used by MainWindow, matching the
// terminology already used by viewer/index.html and README.md (section 5's
// result classification: Xuat sac / Tot / Dat / Khong dat). Written as
// explicit \xHH UTF-8 byte escapes (see NamePool.h for why) so they decode
// correctly regardless of the compiler's assumed source encoding.
namespace VnLabels {

inline QString excellent() { return QString::fromUtf8("\x58\x75\xe1\xba\xa5\x74\x20\x73\xe1\xba\xaf\x63"); }         // Xuất sắc
inline QString good() { return QString::fromUtf8("\x54\xe1\xbb\x91\x74"); }                                         // Tốt
inline QString ok() { return QString::fromUtf8("\xc4\x90\xe1\xba\xa1\x74"); }                                       // Đạt
inline QString fail() { return QString::fromUtf8("\x4b\x68\xc3\xb4\x6e\x67\x20\xc4\x91\xe1\xba\xa1\x74"); }         // Không đạt
inline QString personWord() { return QString::fromUtf8("\x4e\x67\xc6\xb0\xe1\xbb\x9d\x69"); }                       // Người
inline QString targetWord() { return QString::fromUtf8("\x4d\xe1\xbb\xa5\x63\x20\x74\x69\xc3\xaa\x75"); }           // Mục tiêu
inline QString shotWord() { return QString::fromUtf8("\x50\x68\xc3\xa1\x74\x20\x62\xe1\xba\xaf\x6e"); }             // Phát bắn
inline QString synced() { return QString::fromUtf8("\xc4\x90\xc3\xa3\x20\xc4\x91\xe1\xbb\x93\x6e\x67\x20\x62\xe1\xbb\x99"); } // Đã đồng bộ
inline QString lanUnavailable()
{
    // Mất kết nối - đang giữ hàng chờ cục bộ
    return QString::fromUtf8(
        "\x4d\xe1\xba\xa5\x74\x20\x6b\xe1\xba\xbf\x74\x20\x6e\xe1\xbb\x91\x69\x20\x2d\x20\xc4\x91\x61\x6e\x67"
        "\x20\x67\x69\xe1\xbb\xaf\x20\x68\xc3\xa0\x6e\x67\x20\x63\x68\xe1\xbb\x9d\x20\x63\xe1\xbb\xa5\x63\x20\x62\xe1\xbb\x99");
}
inline QString startButton() { return QString::fromUtf8("\x42\xe1\xba\xaf\x74\x20\xc4\x91\xe1\xba\xa7\x75"); } // Bắt đầu
inline QString stopButton() { return QString::fromUtf8("\x44\xe1\xbb\xab\x6e\x67"); }                          // Dừng
inline QString running() { return QString::fromUtf8("\xc4\x90\x61\x6e\x67\x20\x63\x68\xe1\xba\xa1\x79\x2e\x2e\x2e"); } // Đang chạy...
inline QString done() { return QString::fromUtf8("\x48\x6f\xc3\xa0\x6e\x20\x74\x68\xc3\xa0\x6e\x68"); }        // Hoàn thành
inline QString scoreWord() { return QString::fromUtf8("\xc4\x90\x69\xe1\xbb\x83\x6d"); }                       // Điểm

// Reset-local-data (testing helper): a button next to Start/Stop that
// deletes this lane's local sqlite db + rendered images, mirroring
// edge.py's --reset. Confirmed via a QMessageBox before anything is deleted
// since it's irreversible.
inline QString resetButton() { return QString::fromUtf8("\x58\xc3\xb3\x61\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75"); } // Xóa dữ liệu
inline QString resetConfirmTitle() { return QString::fromUtf8("\x58\xc3\xa1\x63\x20\x6e\x68\xe1\xba\xad\x6e\x20\x78\xc3\xb3\x61\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75"); } // Xác nhận xóa dữ liệu
inline QString resetConfirmText() { return QString::fromUtf8("\x58\xc3\xb3\x61\x20\x74\x6f\xc3\xa0\x6e\x20\x62\xe1\xbb\x99\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75\x20\x63\xe1\xbb\xa5\x63\x20\x62\xe1\xbb\x99\x20\x63\xe1\xbb\xa7\x61\x20\x6c\xc3\xa0\x6e\x20\x25\x31\x20\x28\x66\x69\x6c\x65\x20\x63\xc6\xa1\x20\x73\xe1\xbb\x9f\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75\x20\x76\xc3\xa0\x20\xe1\xba\xa3\x6e\x68\x20\x62\x69\x61\x29\x3f\x20\x4b\x68\xc3\xb4\x6e\x67\x20\x74\x68\xe1\xbb\x83\x20\x68\x6f\xc3\xa0\x6e\x20\x74\xc3\xa1\x63\x2e"); } // Xóa toàn bộ dữ liệu cục bộ của làn %1 (file cơ sở dữ liệu và ảnh bia)? Không thể hoàn tác.
inline QString resetDoneText() { return QString::fromUtf8("\xc4\x90\xc3\xa3\x20\x78\xc3\xb3\x61\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75\x20\x63\xe1\xbb\xa5\x63\x20\x62\xe1\xbb\x99\x20\x63\xe1\xbb\xa7\x61\x20\x6c\xc3\xa0\x6e\x20\x25\x31\x2e"); } // Đã xóa dữ liệu cục bộ của làn %1.
inline QString resetNothingText() { return QString::fromUtf8("\x4c\xc3\xa0\x6e\x20\x25\x31\x3a\x20\x6b\x68\xc3\xb4\x6e\x67\x20\x63\xc3\xb3\x20\x64\xe1\xbb\xaf\x20\x6c\x69\xe1\xbb\x87\x75\x20\x63\xe1\xbb\xa5\x63\x20\x62\xe1\xbb\x99\x20\xc4\x91\xe1\xbb\x83\x20\x78\xc3\xb3\x61\x2e"); } // Làn %1: không có dữ liệu cục bộ để xóa.

// Roster (shooter list) UI, added alongside the --people mode: lets the user
// build a list of {name, unit} entries in a table, generate one
// automatically, and have Start run through the list in order.
inline QString rosterTitle() { return QString::fromUtf8("\x44\x61\x6e\x68\x20\x73\xc3\xa1\x63\x68\x20\x78\xe1\xba\xa1\x20\x74\x68\xe1\xbb\xa7"); } // Danh sách xạ thủ
inline QString addRow() { return QString::fromUtf8("\x54\x68\xc3\xaa\x6d"); } // Thêm
inline QString removeRow() { return QString::fromUtf8("\x58\xc3\xb3\x61"); } // Xóa
inline QString generate() { return QString::fromUtf8("\x54\xe1\xba\xa1\x6f\x20\x74\xe1\xbb\xb1\x20\xc4\x91\xe1\xbb\x99\x6e\x67"); } // Tạo tự động
inline QString genShooterCountLabel() { return QString::fromUtf8("\x53\xe1\xbb\x91\x20\x78\xe1\xba\xa1\x20\x74\x68\xe1\xbb\xa7"); } // Số xạ thủ
inline QString genUnitCountLabel() { return QString::fromUtf8("\x53\xe1\xbb\x91\x20\xc4\x91\xc6\xa1\x6e\x20\x76\xe1\xbb\x8b"); } // Số đơn vị
inline QString colName() { return QString::fromUtf8("\x54\xc3\xaa\x6e"); } // Tên
inline QString colUnitName() { return QString::fromUtf8("\xc4\x90\xc6\xa1\x6e\x20\x76\xe1\xbb\x8b"); } // Đơn vị
inline QString colUnitCode() { return QString::fromUtf8("\x4d\xc3\xa3\x20\xc4\x91\xc6\xa1\x6e\x20\x76\xe1\xbb\x8b"); } // Mã đơn vị
inline QString colShooterCode() { return QString::fromUtf8("\x4d\xc3\xa3\x20\x78\xe1\xba\xa1\x20\x74\x68\xe1\xbb\xa7"); } // Mã xạ thủ
inline QString useRosterHint() { return QString::fromUtf8("\x44\x61\x6e\x68\x20\x73\xc3\xa1\x63\x68\x20\x73\xe1\xba\xbd\x20\xc4\x91\xc6\xb0\xe1\xbb\xa3\x63\x20\x64\xc3\xb9\x6e\x67\x20\x6b\x68\x69\x20\x62\xe1\xba\xa5\x6d\x20\x42\xe1\xba\xaf\x74\x20\xc4\x91\xe1\xba\xa7\x75"); } // Danh sách sẽ được dùng khi bấm Bắt đầu
inline QString genUnitPrefix() { return QString::fromUtf8("\xc4\x90\xc6\xa1\x6e\x20\x76\xe1\xbb\x8b"); } // Đơn vị (prefix for auto-generated unit names, "Đơn vị 1", "Đơn vị 2", ...)

// Same thresholds as server.py's SCORE_BANDS (EXCELLENT >=9, GOOD >=7, OK >=5, else FAIL).
inline QString classify(double score)
{
    if (score >= 9.0) return excellent();
    if (score >= 7.0) return good();
    if (score >= 5.0) return ok();
    return fail();
}

} // namespace VnLabels
