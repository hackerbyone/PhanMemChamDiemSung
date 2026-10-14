#include "SyncClient.h"

#include <QEventLoop>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTimer>
#include <QUrl>

SyncClient::SyncClient(QObject *parent)
    : QObject(parent)
{
}

SyncClient::Result SyncClient::postSync(const QString &serverBaseUrl, const QJsonObject &body, int timeoutMs)
{
    Result res;

    QUrl url(serverBaseUrl + QStringLiteral("/api/sync"));
    QNetworkRequest req(url);
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    const QByteArray data = QJsonDocument(body).toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_nam.post(req, data);

    QEventLoop loop;
    QTimer timeoutTimer;
    timeoutTimer.setSingleShot(true);
    bool timedOut = false;
    QObject::connect(&timeoutTimer, &QTimer::timeout, &loop, [&]() {
        timedOut = true;
        loop.quit();
    });
    QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    timeoutTimer.start(timeoutMs);
    loop.exec();

    if (timedOut) {
        reply->abort();
        res.ok = false;
        res.errorString = QStringLiteral("LAN unavailable (timeout after %1 ms); keeping local queue").arg(timeoutMs);
        reply->deleteLater();
        return res;
    }

    res.httpStatus = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    if (reply->error() != QNetworkReply::NoError) {
        res.ok = false;
        res.errorString = reply->errorString();
    } else {
        const QByteArray respData = reply->readAll();
        QJsonParseError perr{};
        const QJsonDocument doc = QJsonDocument::fromJson(respData, &perr);
        if (perr.error == QJsonParseError::NoError && doc.isObject())
            res.responseJson = doc.object();
        res.ok = (res.httpStatus >= 200 && res.httpStatus < 300);
        if (!res.ok && res.errorString.isEmpty())
            res.errorString = QStringLiteral("HTTP %1: %2").arg(res.httpStatus).arg(QString::fromUtf8(respData));
    }
    reply->deleteLater();
    return res;
}
