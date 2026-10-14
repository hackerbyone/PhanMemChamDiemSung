#pragma once
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QString>

// Posts one /api/sync batch and blocks (via a local QEventLoop) until the
// reply arrives or times out -- the same "fire it and wait" shape as
// edge.py's `requests.post(..., timeout=5)`. Meant to be constructed and
// used from a background QThread (EdgeWorker), never from the GUI thread,
// so blocking here never freezes the window; QThread::run() has no event
// loop of its own to block, so the local QEventLoop below is what actually
// lets the QNetworkAccessManager's signals get processed while we wait.
class SyncClient : public QObject
{
    Q_OBJECT
public:
    explicit SyncClient(QObject *parent = nullptr);

    struct Result
    {
        bool ok = false;
        int httpStatus = 0;
        QString errorString;
        QJsonObject responseJson;
    };

    // POSTs `body` as JSON to `serverBaseUrl` + "/api/sync".
    Result postSync(const QString &serverBaseUrl, const QJsonObject &body, int timeoutMs = 5000);

private:
    QNetworkAccessManager m_nam;
};
