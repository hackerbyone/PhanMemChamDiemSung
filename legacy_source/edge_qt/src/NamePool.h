#pragma once
#include <QString>
#include <QStringList>

// Mirrors edge.py's VN_SHOOTER_NAMES list exactly (same 10 names, same
// order), so a lane simulated by this Qt/C++ edge looks consistent with one
// simulated by edge.py. Every name is written as an explicit \xHH UTF-8 byte
// escape sequence (never a raw non-ASCII character in the source file) so
// the string is decoded correctly regardless of the compiler's assumed
// source-file encoding (this matters especially for MSVC on Windows, which
// can otherwise mis-detect a UTF-8 source file with no byte-order mark).
inline const QStringList &vnShooterNames()
{
    static const QStringList names = {
        QString::fromUtf8("\x4e\x67\x75\x79\xe1\xbb\x85\x6e\x20\x56\xc4\x83\x6e\x20\x41"), // Nguyễn Văn A
        QString::fromUtf8("\x54\x72\xe1\xba\xa7\x6e\x20\x58\x75\xc3\xa2\x6e\x20\x42"),     // Trần Xuân B
        QString::fromUtf8("\x4c\xc3\xaa\x20\x54\x68\xe1\xbb\x8b\x20\x43"),                 // Lê Thị C
        QString::fromUtf8("\x50\x68\xe1\xba\xa1\x6d\x20\x56\xc4\x83\x6e\x20\x44"),         // Phạm Văn D
        QString::fromUtf8("\x48\x6f\xc3\xa0\x6e\x67\x20\x54\x68\xe1\xbb\x8b\x20\x45"),     // Hoàng Thị E
        QString::fromUtf8("\xc4\x90\xe1\xbb\x97\x20\x56\xc4\x83\x6e\x20\x46"),             // Đỗ Văn F
        QString::fromUtf8("\x56\xc5\xa9\x20\x54\x68\xe1\xbb\x8b\x20\x47"),                 // Vũ Thị G
        QString::fromUtf8("\x42\xc3\xb9\x69\x20\x56\xc4\x83\x6e\x20\x48"),                 // Bùi Văn H
        QString::fromUtf8("\x4e\x67\xc3\xb4\x20\x56\xc4\x83\x6e\x20\x49"),                 // Ngô Văn I
        QString::fromUtf8("\x44\xc6\xb0\xc6\xa1\x6e\x67\x20\x54\x68\xe1\xbb\x8b\x20\x4b"), // Dương Thị K
    };
    return names;
}

// Same lane -> name cycling rule as edge.py's default_shooter_name().
inline QString defaultShooterName(int lane)
{
    const QStringList &names = vnShooterNames();
    int idx = (lane - 1) % names.size();
    if (idx < 0)
        idx += names.size();
    return names.at(idx);
}
