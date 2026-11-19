/**
 * GAS -> Nexus の接続確認専用。Web アプリとしてのデプロイやトリガーは不要。
 * スクリプトプロパティ NEXUS_BASE_URL に https://example.com の形式で設定し、
 * testNexusConnectivity をエディタから手動実行する。
 * 認証情報・個人情報の送信、シート操作、メール操作、ユーザー作成は行わない。
 */
function testNexusConnectivity() {
  var origin = (PropertiesService.getScriptProperties()
    .getProperty('NEXUS_BASE_URL') || '').trim().replace(/\/$/, '');
  var match = /^https:\/\/([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?$/i.exec(origin);
  if (!match || (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535))) {
    console.log('設定エラー: NEXUS_BASE_URL に HTTPS のドメイン（必要ならポート付き）のみ設定してください。');
    return;
  }

  var response;
  try {
    response = UrlFetchApp.fetch(origin + '/users/applications/', {
      method: 'get',
      followRedirects: false,
      muteHttpExceptions: true,
      validateHttpsCertificates: true
    });
  } catch (error) {
    // 例外原文には URL 等が含まれ得るため出力しない。
    var reason = classifyNexusConnectionError_(error);
    console.log('CONNECTION_ERROR=' + reason);
    console.log('接続失敗: HTTP応答なし。上記分類は例外文からの推定であり、原因の確定ではありません。');
    return;
  }

  var status = response.getResponseCode();
  console.log('HTTP_STATUS=' + status);
  if (status === 301 || status === 302 || status === 303 || status === 307 || status === 308) {
    console.log('HTTPS応答あり。ログイン画面等への転送の可能性があります。転送先には接続していません。');
  } else if (status >= 200 && status < 300) {
    console.log('HTTPS応答あり。通信先がNexus自身かはサーバー側でも確認してください。');
  } else if (status === 401 || status === 403) {
    console.log('HTTPS応答あり。ただし認証またはアクセス制限で拒否されています。Nexusではなく手前のプロキシ等の応答の場合もあります。');
  } else {
    console.log('HTTPS応答あり。ただし想定外のステータスです。接続先やサーバー側の状態を確認してください。');
  }
  console.log('この試験では専用APIの認証、フォーム連携、Gmail権限は未検証です。');
  // 応答本文、ヘッダー、転送URLはログに出さない。
}

/** 例外原文は分類にだけ使用し、呼び出し元にも返さない。 */
function classifyNexusConnectionError_(error) {
  var message = String(error && error.message || error || '');
  if (/permission|not authorized|authorization|scope|アクセス権|権限|承認|許可/i.test(message)) {
    return 'PERMISSION（GASの実行権限・外部通信の組織ポリシーを確認）';
  }
  if (/dns|resolve|host.*not found|名前解決/i.test(message)) {
    return 'DNS（公開DNSで解決できるドメインか確認）';
  }
  if (/certificate|ssl|tls|証明書/i.test(message)) {
    return 'TLS（証明書の有効期限・ホスト名・証明書チェーンを確認）';
  }
  if (/timeout|timed out|タイムアウト/i.test(message)) {
    return 'TIMEOUT（公開到達性・ポート・ファイアウォールを確認）';
  }
  if (/address unavailable|connection|connect|接続|アドレスを利用できません/i.test(message)) {
    return 'NETWORK（内部専用アドレス・ポート・接続拒否などを確認）';
  }
  if (/quota|too many|limit exceeded|割り当て|上限/i.test(message)) {
    return 'QUOTA（GASの利用上限を確認）';
  }
  return 'UNKNOWN（この例外文では分類不能）';
}