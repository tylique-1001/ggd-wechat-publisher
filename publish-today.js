#!/usr/bin/env node
/**
 * publish-today.js
 *
 * 由 GitHub Actions cron 触发，读取 schedule.json，
 * 发布今天的贴图和文章（如果是文章日）到公众号草稿箱。
 *
 * 用法：node publish-today.js
 * 环境变量：WECHAT_APPID, WECHAT_SECRET（GitHub Secrets）
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

// ─── 从环境变量读取凭证（GitHub Secrets） ───
const APPID = process.env.WECHAT_APPID || 'wx51779815b6bc189c';
const APPSECRET = process.env.WECHAT_SECRET || '64e66fb2e99864d283759e1053f1ab23';
const AUTHOR = 'Zylon';

// ─── HTTP helpers ───
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    lib.get(url, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    }).on('error', reject);
  });
}

function httpPostJson(url, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const payload = JSON.stringify(body);
    const opts = {
      hostname: u.hostname, port: u.port || 443,
      path: u.pathname + u.search, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
    };
    const lib = u.protocol === 'https:' ? https : http;
    const req = lib.request(opts, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

// ─── WeChat API ───
let tokenCache = null;

async function getToken() {
  if (tokenCache && Date.now() < tokenCache.expires - 300000) return tokenCache.token;
  const d = await httpGet(`https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${APPID}&secret=${APPSECRET}`);
  if (d.errcode) throw new Error(`token: ${d.errcode} ${d.errmsg}`);
  tokenCache = { token: d.access_token, expires: Date.now() + d.expires_in * 1000 };
  console.log(`✅ Token OK`);
  return tokenCache.token;
}

// 预上传的默认封面永久 media_id（暖色渐变 PNG）
const DEFAULT_COVER_MEDIA_ID = '93YlCggwjITUpvxluS5h6TPm2Ls0T65Kq2LgJ8VzjeY515BzEiSuSBKk130MQ_W0';

/**
 * 上传封面图片到永久素材库，返回 media_id
 */
function uploadCover(token, imagePath) {
  return new Promise((resolve, reject) => {
    const boundary = '----Cover' + Date.now();
    const img = fs.readFileSync(imagePath);
    const filename = path.basename(imagePath);
    const header = Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="media"; filename="${filename}"\r\nContent-Type: image/png\r\n\r\n`
    );
    const footer = Buffer.from(`\r\n--${boundary}--\r\n`);
    const body = Buffer.concat([header, img, footer]);

    const req = https.request({
      hostname: 'api.weixin.qq.com',
      path: `/cgi-bin/material/add_material?access_token=${token}&type=image`,
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': body.length },
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const r = JSON.parse(data);
          if (r.errcode) reject(new Error(`uploadCover: ${r.errcode} ${r.errmsg}`));
          else resolve(r.media_id);
        } catch(e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function createDraft(token, title, digest, contentHtml, coverImagePath) {
  let thumbMediaId = DEFAULT_COVER_MEDIA_ID;

  // 如果有自定义封面图，上传并替换
  if (coverImagePath && fs.existsSync(coverImagePath)) {
    try {
      thumbMediaId = await uploadCover(token, coverImagePath);
      console.log(`  📸 自定义封面上传成功: ${thumbMediaId}`);
    } catch (e) {
      console.log(`  ⚠️ 封面图上传失败，使用默认封面: ${e.message}`);
    }
  }

  const payload = {
    articles: [{
      title,
      author: AUTHOR,
      digest: digest || '',
      content: contentHtml,
      thumb_media_id: thumbMediaId,
      need_open_comment: 1,
      only_fans_can_comment: 0,
    }],
  };

  // 微信 API 偶发 40007，加重试（最多 3 次，间隔 2s）
  let lastError;
  for (let i = 0; i < 3; i++) {
    const d = await httpPostJson(`https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${token}`, payload);
    if (!d.errcode) return d.media_id;
    lastError = new Error(`draft: ${d.errcode} ${d.errmsg}`);
    if (d.errcode !== 40007) throw lastError;
    console.log(`  ⚠️ 40007 重试 ${i + 1}/3...`);
    await new Promise(r => setTimeout(r, 2000));
  }
  throw lastError;
}

// ─── Beijing time ───
function beijingNow() {
  return new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
}

function todayKey() {
  const d = beijingNow();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function isArticleDay() {
  return [2, 4, 6].includes(beijingNow().getDay()); // Tue/Thu/Sat
}

// ─── 防重复推送：查询今天已有的草稿 ───
/**
 * 查询草稿箱中所有草稿标题（用于防重复，纯标题匹配）
 * 策略：每天每个标题唯一，所以只要标题已存在就跳过，
 * 第二天排期表中的标题不同，不会误判。
 */
async function checkExistingDrafts(token) {
  try {
    const d = await httpPostJson(`https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=${token}`, {
      offset: 0,
      count: 20,
      no_content: 1,
    });
    if (d.errcode) {
      console.log(`  ⚠️ 草稿查询失败(err=${d.errcode})，降级：允许推送`);
      return [];
    }
    const titles = [];
    for (const item of (d.item || [])) {
      const newsList = (item.content && item.content.news_item) ? item.content.news_item : [];
      for (const n of newsList) {
        if (n.title) titles.push(n.title);
      }
    }
    if (titles.length > 0) {
      console.log(`  📋 草稿箱现有标题(${titles.length}条): ${titles.slice(0,5).map(t => `"${t}"`).join(', ')}${titles.length > 5 ? '...' : ''}`);
    }
    return titles;
  } catch (e) {
    console.log(`  ⚠️ 草稿查询异常: ${e.message}，降级：允许推送`);
    return [];
  }
}

// ─── Main ───
async function main() {
  const today = todayKey();
  const dow = ['日','一','二','三','四','五','六'][beijingNow().getDay()];
  console.log(`📅 今日: ${today} (周${dow})`);

  const scheduleFile = path.join(__dirname, 'content', 'schedule.json');
  if (!fs.existsSync(scheduleFile)) {
    console.log('⚠️ schedule.json 不存在，跳过');
    return;
  }

  const schedule = JSON.parse(fs.readFileSync(scheduleFile, 'utf-8'));
  const todayEntries = schedule.filter(e => e.date === today);

  if (todayEntries.length === 0) {
    console.log('ℹ️ 今日无推送计划');
    return;
  }

  console.log(`📋 今日 ${todayEntries.length} 条待推送`);
  const platform = process.env.PLATFORM || 'local';
  console.log(`🖥️ 执行平台: ${platform}`);
  const token = await getToken();

  // 先查今天已有草稿，避免多平台重复推送
  const existingTitles = await checkExistingDrafts(token);

  let pushed = 0;
  let skipped = 0;
  let failed = 0;

  for (const entry of todayEntries) {
    // 文章只在二/四/六发
    if (entry.type === 'article' && !isArticleDay()) {
      console.log(`⏭️ 跳过文章（非文章日）: ${entry.title}`);
      skipped++;
      continue;
    }

    // 防重复：今天已有同标题草稿
    if (existingTitles.includes(entry.title)) {
      console.log(`⏭️ 跳过（草稿箱已存在）: "${entry.title}"`);
      skipped++;
      continue;
    }

    const subDir = entry.type === 'article' ? 'articles' : 'images';
    const contentFile = path.join(__dirname, 'content', subDir, entry.content_file);

    if (!fs.existsSync(contentFile)) {
      console.log(`⚠️ 内容文件缺失: ${entry.content_file}`);
      skipped++;
      continue;
    }

    const html = fs.readFileSync(contentFile, 'utf-8');
    const coverPath = (entry.cover_image)
      ? path.join(__dirname, 'content', 'covers', entry.cover_image)
      : null;
    try {
      const mediaId = await createDraft(token, entry.title, entry.digest || '', html, coverPath);
      console.log(`✅ [${entry.type}] "${entry.title}" → media_id: ${mediaId}`);
      pushed++;
    } catch (e) {
      console.log(`❌ [${entry.type}] "${entry.title}" 失败: ${e.message}`);
      failed++;
    }
  }

  console.log(`🎉 今日推送完成 | 平台=${platform} 成功=${pushed} 跳过=${skipped} 失败=${failed}`);
}

main().catch(e => {
  console.error(`💥 致命错误: ${e.message}`);
  process.exit(1);
});
