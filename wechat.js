/**
 * WeChat Official Account API wrapper — zero dependencies (Node.js built-ins only)
 * Handles: access_token, image upload, draft creation
 */

const https = require('https');
const http = require('http');
const fs = require('fs');

// ─── Credentials ───
const APPID = 'wx51779815b6bc189c';
const APPSECRET = '64e66fb2e99864d283759e1053f1ab23';
const AUTHOR = 'Zylon';

// ─── Token cache ───
let tokenCache = { access_token: null, expires_at: 0 };

/**
 * Simple HTTP GET request (returns parsed JSON body)
 */
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    lib.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(`JSON parse error: ${data.slice(0, 200)}`)); }
      });
    }).on('error', reject);
  });
}

/**
 * HTTP POST with JSON body
 */
function httpPostJson(url, body) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const payload = JSON.stringify(body);
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || 443,
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    };
    const lib = parsed.protocol === 'https:' ? https : http;
    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(`JSON parse error: ${data.slice(0, 200)}`)); }
      });
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

/**
 * HTTP POST with multipart/form-data (for image upload)
 */
function httpPostMultipart(url, fieldName, filePath) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const boundary = '----WechatPublisher' + Date.now();
    const fileBuffer = fs.readFileSync(filePath);
    const filename = filePath.split('/').pop();

    const header = Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="${fieldName}"; filename="${filename}"\r\n` +
      `Content-Type: image/png\r\n\r\n`
    );
    const footer = Buffer.from(`\r\n--${boundary}--\r\n`);
    const body = Buffer.concat([header, fileBuffer, footer]);

    const options = {
      hostname: parsed.hostname,
      port: parsed.port || 443,
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': body.length,
      },
    };
    const lib = parsed.protocol === 'https:' ? https : http;
    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(`JSON parse error: ${data.slice(0, 200)}`)); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ─── Public API ───

async function getAccessToken() {
  const now = Date.now();
  if (tokenCache.access_token && now < tokenCache.expires_at - 300000) {
    return tokenCache.access_token;
  }

  const data = await httpGet(
    `https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${APPID}&secret=${APPSECRET}`
  );

  if (data.errcode) {
    throw new Error(`getAccessToken: ${data.errcode} ${data.errmsg}`);
  }

  tokenCache = {
    access_token: data.access_token,
    expires_at: now + data.expires_in * 1000,
  };
  console.log(`[wechat] Token OK, expires in ${data.expires_in}s`);
  return tokenCache.access_token;
}

async function uploadThumb(token, imagePath) {
  if (!imagePath || !fs.existsSync(imagePath)) return null;

  const data = await httpPostMultipart(
    `https://api.weixin.qq.com/cgi-bin/media/upload?access_token=${token}&type=image`,
    'media',
    imagePath
  );

  if (data.errcode) {
    console.error(`[wechat] uploadThumb error: ${data.errcode} ${data.errmsg}`);
    return null;
  }
  console.log(`[wechat] Thumb OK, media_id: ${data.media_id}`);
  return data.media_id;
}

async function createDraft(token, article) {
  const payload = {
    articles: [{
      title: article.title,
      author: AUTHOR,
      digest: article.digest || '',
      content: article.content_html,
      thumb_media_id: article.thumb_media_id || '',
      need_open_comment: 1,
      only_fans_can_comment: 0,
    }],
  };

  const data = await httpPostJson(
    `https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${token}`,
    payload
  );

  if (data.errcode) {
    throw new Error(`createDraft: ${data.errcode} ${data.errmsg}`);
  }
  console.log(`[wechat] Draft OK, media_id: ${data.media_id}`);
  return data.media_id;
}

module.exports = { getAccessToken, uploadThumb, createDraft, APPID, AUTHOR };
