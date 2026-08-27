/**
 * Google Apps Script 側の通知リレーサンプルです。
 *
 * 1. このスクリプトを Apps Script に貼り付ける
 * 2. CHAT_WEBHOOK_URL を設定する
 * 3. ウェブアプリとしてデプロイする
 * 4. 発行されたURLを Django の GOOGLE_CHAT_GAS_WEB_APP_URL に設定する
 */

const CHAT_WEBHOOK_URL = 'https://chat.googleapis.com/v1/spaces/AAQA1Ou2jsg/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=ivlj0PxB8QBt77xOJNAQVad6qavNdPxBoU-5iTzmupo';

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildDraftHtmlFromParams(params) {
  var introMessage = String((params.intro_message || '')).trim() || '以下のコンテンツが承認されました。';
  var managementCode = String((params.management_code || '-')).trim() || '-';
  var articleTitle = String((params.article_title || '-')).trim() || '-';
  var approverName = String((params.approver_name || '-')).trim() || '-';
  var approvedAt = String((params.approved_at || '-')).trim() || '-';
  var knowledgeUrl = String((params.knowledge_url || '')).trim();
  var remandReason = String((params.remand_reason || '')).trim();

  var rows = [
    ['管理番号', managementCode],
    ['タイトル', articleTitle],
    ['承認者', approverName],
    ['承認日時', approvedAt],
  ];
  var rowsHtml = rows.map(function(row) {
    return '<tr>'
      + '<th style="padding:8px 12px;border:1px solid #E5E7EB;background:#FDE9D9;text-align:left;">' + escapeHtml(row[0]) + '</th>'
      + '<td style="padding:8px 12px;border:1px solid #E5E7EB;">' + escapeHtml(row[1]) + '</td>'
      + '</tr>';
  }).join('');

  var remandHtml = remandReason ? ('<p style="margin:12px 0 0;">差戻し理由: ' + escapeHtml(remandReason) + '</p>') : '';
  var linkHtml = knowledgeUrl
    ? ('<p style="margin:12px 0 0;"><a href="' + escapeHtml(knowledgeUrl) + '">ナレッジのリンク（' + escapeHtml(knowledgeUrl) + '）</a></p>')
    : '';

  return '<div style="font-family:Meiryo,\'Yu Gothic\',sans-serif;font-size:14px;color:#1F2937;line-height:1.6;">'
    + '<p style="margin:0 0 12px;">' + escapeHtml(introMessage) + '</p>'
    + '<table style="border-collapse:collapse;border:1px solid #E5E7EB;">'
    + rowsHtml
    + '</table>'
    + remandHtml
    + linkHtml
    + '</div>';
}

/**
 * ブラウザからのGETリクエストを処理する。
 * action=create_draft の場合: Gmail下書きを作成してDjangoコールバックへリダイレクト
 */
function doGet(e) {
  try {
    var action = (e && e.parameter && e.parameter.action) ? String(e.parameter.action).trim() : '';
    Logger.log('doGet start action=%s params=%s', action, JSON.stringify((e && e.parameter) ? e.parameter : {}));

    if (action === 'create_draft') {
      var to       = String((e.parameter.to       || '')).trim();
      var cc       = String((e.parameter.cc       || '')).trim();
      var subject  = String((e.parameter.subject  || 'Nexus 下書き')).trim();
      var body     = String((e.parameter.body     || '')).trim();
      var htmlBody = String((e.parameter.html_body || '')).trim();
      var callback = String((e.parameter.callback || '')).trim();

      if (!to) {
        throw new Error('to が空のため下書きを作成できません。');
      }

      var draftOptions = {};
      if (cc) {
        draftOptions.cc = cc;
      }
      if (!htmlBody) {
        htmlBody = buildDraftHtmlFromParams((e && e.parameter) ? e.parameter : {});
      }
      if (htmlBody) {
        draftOptions.htmlBody = htmlBody;
      }
      Logger.log('create_draft requested to=%s cc=%s subject=%s bodyLength=%s htmlBodyLength=%s callback=%s', to, cc, subject, body.length, htmlBody.length, callback);
      var draft = GmailApp.createDraft(to, subject, body, draftOptions);
      var draftId = draft.getId();
      Logger.log('create_draft success draftId=%s', draftId);

      if (callback) {
        var sep = callback.indexOf('?') >= 0 ? '&' : '?';
        var redirectUrl = callback + sep + 'draft_id=' + encodeURIComponent(draftId);
        // <meta refresh> を主リダイレクト手段にし、<script> をフォールバックとして両方記述
        return HtmlService.createHtmlOutput(
          '<!DOCTYPE html><html><head>'
          + '<meta http-equiv="refresh" content="0;url=' + redirectUrl + '">'
          + '</head><body>'
          + '<p>Gmail下書きを作成しました。Nexusへ戻ります...</p>'
          + '<script>try{window.top.location.href=' + JSON.stringify(redirectUrl) + ';}catch(ex){window.location.href=' + JSON.stringify(redirectUrl) + ';}<\/script>'
          + '</body></html>'
        ).setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
      }

      return ContentService.createTextOutput(
        JSON.stringify({ ok: true, action: 'create_draft', draftId: draftId })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    var infoMessage = action ? ('未対応のactionです: ' + action) : '処理しました。';
    return HtmlService.createHtmlOutput(
      '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
      + '<script>alert(' + JSON.stringify(infoMessage) + ');</script>'
      + '<p>' + infoMessage + '</p>'
      + '</body></html>'
    ).setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);

  } catch (err) {
    var callback2 = (e && e.parameter && e.parameter.callback) ? String(e.parameter.callback).trim() : '';
    var errorMsg = String(err && err.message ? err.message : err);
    Logger.log('doGet error action=%s error=%s', (e && e.parameter && e.parameter.action) ? String(e.parameter.action).trim() : '', errorMsg);

    if (callback2) {
      var sep2 = callback2.indexOf('?') >= 0 ? '&' : '?';
      var redirectUrl2 = callback2 + sep2 + 'error_message=' + encodeURIComponent(errorMsg);
      return HtmlService.createHtmlOutput(
        '<!DOCTYPE html><html><head>'
        + '<meta http-equiv="refresh" content="0;url=' + redirectUrl2 + '">'
        + '</head><body>'
        + '<p>エラーが発生しました: ' + errorMsg + '</p>'
        + '<script>try{window.top.location.href=' + JSON.stringify(redirectUrl2) + ';}catch(ex){window.location.href=' + JSON.stringify(redirectUrl2) + ';}<\/script>'
        + '</body></html>'
      ).setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
    }

    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, action: 'error', body: errorMsg })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

function doPost(e) {
  try {
    const raw = (e && e.postData && e.postData.contents) || '{}';
    Logger.log('doPost start raw=%s', raw);
    const payload = JSON.parse(raw);
    const action = String(payload.action || 'chat_message').trim();

    if (action === 'create_draft') {
      const to = String(payload.to || '').trim();
      const cc = String(payload.cc || '').trim();
      const subject = String(payload.subject || 'Nexus 下書き').trim();
      const body = String(payload.body || '').trim();
      let htmlBody = String(payload.html_body || '').trim();

      if (!to) {
        return ContentService.createTextOutput(
          JSON.stringify({
            ok: false,
            action: 'create_draft',
            body: 'to が空のため下書きを作成できません。',
          })
        ).setMimeType(ContentService.MimeType.JSON);
      }

      const options = {};
      if (cc) {
        options.cc = cc;
      }
      if (!htmlBody) {
        htmlBody = buildDraftHtmlFromParams(payload);
      }
      if (htmlBody) {
        options.htmlBody = htmlBody;
      }
      const draft = GmailApp.createDraft(to, subject, body, options);
      Logger.log('doPost create_draft success draftId=%s to=%s', draft.getId(), to);
      return ContentService.createTextOutput(
        JSON.stringify({
          ok: true,
          action: 'create_draft',
          draftId: draft.getId(),
        })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    const text = String(payload.text || '通知テスト').trim() || '通知テスト';

    if (action === 'post_image_chart') {
      const base64Data = payload.image_base64;
      let imageUrl = '';
      if (base64Data) {
        // Base64データをデコードしてGoogleドライブに保存
        const decoded = Utilities.base64Decode(base64Data.replace(/^data:image\/\w+;base64,/, ''));
        const blob = Utilities.newBlob(decoded, 'image/png', 'nexus_monthly_chart.png');
        const file = DriveApp.createFile(blob);
        file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
        
        // 直リンク画像URL（Googleドライブのプレビュー/ダウンロードURL）
        imageUrl = "https://drive.google.com/uc?export=download&id=" + file.getId();
        Logger.log('Image saved in Google Drive. ID=%s, URL=%s', file.getId(), imageUrl);
      }

      // cardsV2 リッチカードを構築
      const cardPayload = {
        cardsV2: [{
          cardId: 'monthlyChartCard',
          card: {
            header: {
              title: '【Nexus】当月のメンバー投稿状況',
              subtitle: 'アナライズ'
            },
            sections: [
              {
                widgets: [
                  {
                    textParagraph: {
                      text: text.replace(/\n/g, '<br>')
                    }
                  }
                ]
              }
            ]
          }
        }]
      };

      if (imageUrl) {
        cardPayload.cardsV2[0].card.sections.push({
          widgets: [
            {
              image: {
                imageUrl: imageUrl
              }
            }
          ]
        });
      }

      const response = UrlFetchApp.fetch(CHAT_WEBHOOK_URL, {
        method: 'post',
        contentType: 'application/json; charset=UTF-8',
        payload: JSON.stringify(cardPayload),
        muteHttpExceptions: true,
      });

      return ContentService.createTextOutput(
        JSON.stringify({
          ok: response.getResponseCode() >= 200 && response.getResponseCode() < 300,
          action: 'post_image_chart',
          status: response.getResponseCode(),
          body: response.getContentText(),
          driveFileId: imageUrl ? file.getId() : null,
        })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    const response = UrlFetchApp.fetch(CHAT_WEBHOOK_URL, {
      method: 'post',
      contentType: 'application/json; charset=UTF-8',
      payload: JSON.stringify({ text }),
      muteHttpExceptions: true,
    });

    return ContentService.createTextOutput(
      JSON.stringify({
        ok: response.getResponseCode() >= 200 && response.getResponseCode() < 300,
        action: 'chat_message',
        status: response.getResponseCode(),
        body: response.getContentText(),
      })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    Logger.log('doPost error=%s', String(err && err.message ? err.message : err));
    return ContentService.createTextOutput(
      JSON.stringify({
        ok: false,
        action: 'error',
        body: String(err && err.message ? err.message : err),
      })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}