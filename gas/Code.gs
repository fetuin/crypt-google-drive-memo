/**
 * 鍵メモ（Google Apps Script版）サーバー側  v2
 *
 * - マイドライブの「鍵メモ」フォルダ内のファイルを一覧・読み書きします。
 * - 暗号化と復号はすべてブラウザ側で行い、ここには暗号文しか届きません。
 * - 画像・動画などのファイルは、ブラウザからDrive APIへ直接アップロード／ダウンロードします
 *   （Apps Scriptの容量制限を受けないようにするため）。そのためのアクセストークンを渡します。
 * - URL末尾に ?id=<ファイルID> を付けると、そのファイルを直接開きます。
 * - デプロイ設定: 種類「ウェブアプリ」/ 実行ユーザー「自分」/ アクセス「自分のみ」
 */

const FOLDER_NAME = '鍵メモ';
const MAGIC = '#kagimemo v1 ';

function doGet() {
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('鍵メモ')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

function getFolder_() {
  const it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

// ファイル内容のハッシュ。ほかの端末での更新（競合）検出に使う
function rev_(s) {
  return Utilities.base64Encode(
    Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8));
}

function listFiles() {
  const files = getFolder_().getFiles();
  const out = [];
  while (files.hasNext()) {
    const f = files.next();
    if (f.isTrashed()) continue;
    out.push({ id: f.getId(), name: f.getName(), updated: f.getLastUpdated().getTime(), size: f.getSize() });
  }
  out.sort((a, b) => b.updated - a.updated);
  return out;
}

/**
 * ブラウザからDrive APIを直接呼ぶための情報。
 * トークンは約1時間有効で、このウェブアプリを開いた本人の権限でのみ発行されます。
 */
function getDriveAccess() {
  return { token: ScriptApp.getOAuthToken(), folderId: getFolder_().getId() };
}

// ---------- テキストメモ（v1形式） ----------

function readFile(id) {
  const f = getOwnFile_(id);
  const content = f.getBlob().getDataAsString('UTF-8');
  return { id: f.getId(), name: f.getName(), content: content, rev: rev_(content) };
}

/**
 * req = { id?, name?, content, rev?, force? }
 *  id あり: 上書き保存（rev が現在の内容と違えば CONFLICT。force で強制上書き）
 *  id なし: 「鍵メモ」フォルダに新規作成
 */
function saveFile(req) {
  const content = String(req.content || '');
  if (content.indexOf(MAGIC) !== 0) throw new Error('暗号化されていないデータは保存できません。');

  if (req.id) {
    const f = getOwnFile_(req.id);
    if (!req.force && req.rev && rev_(f.getBlob().getDataAsString('UTF-8')) !== req.rev) {
      throw new Error('CONFLICT');
    }
    f.setContent(content);
    return { id: f.getId(), name: f.getName(), rev: rev_(content) };
  }

  let name = String(req.name || '').trim();
  if (!name) throw new Error('ファイル名を入力してください。');
  if (name.length > 100) throw new Error('ファイル名が長すぎます。');
  if (!/\.txt$/i.test(name)) name += '.txt';
  const folder = getFolder_();
  if (folder.getFilesByName(name).hasNext()) throw new Error('「' + name + '」はすでにあります。別の名前にしてください。');
  const f = folder.createFile(name, content, MimeType.PLAIN_TEXT);
  return { id: f.getId(), name: f.getName(), rev: rev_(content) };
}

// ---------- 共通 ----------

// 「鍵メモ」フォルダ内のファイルだけを扱う
function getOwnFile_(id) {
  let f;
  try { f = DriveApp.getFileById(String(id || '')); } catch (e) { throw new Error('ファイルが見つかりません。'); }
  const parents = f.getParents();
  const folderId = getFolder_().getId();
  while (parents.hasNext()) if (parents.next().getId() === folderId) return f;
  throw new Error('「鍵メモ」フォルダのファイルではありません。');
}

// URL（?id=…）で直接開くときのファイル情報
function getFileInfo(id) {
  const f = getOwnFile_(id);
  if (f.isTrashed()) throw new Error('このファイルはゴミ箱に移動されています。');
  return { id: f.getId(), name: f.getName(), updated: f.getLastUpdated().getTime(), size: f.getSize() };
}

// このウェブアプリのURL（ファイルごとのリンク作成用）
function getAppUrl() {
  return ScriptApp.getService().getUrl();
}

// ゴミ箱へ移動（ドライブのゴミ箱から30日以内なら元に戻せます）
function trashFile(id) {
  getOwnFile_(id).setTrashed(true);
  return true;
}
