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
const LARK_WEBHOOK = process.env.LARK_WEBHOOK || '';

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

// ─── 飞书通知 ───
const TYPE_LABELS = { article: '📰 文章', image: '🖼️ 贴图' };

async function sendLarkNotification(text) {
  if (!LARK_WEBHOOK) {
    console.log('  ⚠️ 飞书通知跳过：LARK_WEBHOOK 未配置');
    return;
  }
  console.log(`  📨 发送飞书通知...`);
  try {
    const payload = { msg_type: 'text', content: { text } };
    const res = await httpPostJson(LARK_WEBHOOK, payload);
    console.log(`  📨 飞书通知响应: ${JSON.stringify(res)}`);
  } catch (e) {
    console.log(`  ⚠️ 飞书通知发送失败: ${e.message}`);
  }
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

// HTML 转纯文本（图片消息 content 仅支持纯文本）
function htmlToText(html) {
  return html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

async function createDraft(token, entry, contentHtml, coverImagePath) {
  const isImageType = entry.type === 'image';

  // 上传封面/图片素材
  let imageMediaId = null;
  if (coverImagePath && fs.existsSync(coverImagePath)) {
    try {
      imageMediaId = await uploadCover(token, coverImagePath);
      console.log(`  📸 图片素材上传成功: ${imageMediaId}`);
    } catch (e) {
      console.log(`  ⚠️ 图片素材上传失败: ${e.message}`);
    }
  }

  let payload;

  if (isImageType) {
    // ─── 图片消息 (newspic) ───
    if (!imageMediaId) {
      throw new Error('图片消息需要至少一张图片素材');
    }
    const textContent = htmlToText(contentHtml);
    payload = {
      articles: [{
        article_type: 'newspic',
        title: entry.title,
        content: textContent,
        need_open_comment: 1,
        only_fans_can_comment: 0,
        image_info: {
          image_list: [
            { image_media_id: imageMediaId }
          ]
        },
        cover_info: {
          crop_percent_list: [
            { ratio: '1_1', x1: '0', y1: '0', x2: '1', y2: '1' }
          ]
        }
      }]
    };
  } else {
    // ─── 图文消息 (news) ───
    const thumbMediaId = imageMediaId || DEFAULT_COVER_MEDIA_ID;
    payload = {
      articles: [{
        article_type: 'news',
        title: entry.title,
        author: AUTHOR,
        digest: entry.digest || '',
        content: contentHtml,
        thumb_media_id: thumbMediaId,
        need_open_comment: 1,
        only_fans_can_comment: 0,
      }]
    };
  }

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
    const typeLabel = TYPE_LABELS[entry.type] || entry.type;
    // 单篇重试：最多3次（首次+2次重试），间隔3秒
    let mediaId = null;
    let pushOk = false;
    let lastErr = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        // 每次重试前刷新 token（防止过期）
        if (attempt > 1) {
          tokenCache = null;
          const freshToken = await getToken();
          // 重试前重新检查草稿箱（防止上次其实成功了）
          const recheckTitles = await checkExistingDrafts(freshToken);
          if (recheckTitles.includes(entry.title)) {
            console.log(`  ⏭️ 重试发现草稿已存在，跳过: "${entry.title}"`);
            pushed++;
            pushOk = true;
            break;
          }
        }
        mediaId = await createDraft(tokenCache ? tokenCache.token : await getToken(), entry, html, coverPath);
        console.log(`✅ [${entry.type}] "${entry.title}" → media_id: ${mediaId}${attempt > 1 ? ` (重试${attempt}次后成功)` : ''}`);
        pushed++;
        pushOk = true;
        break;
      } catch (e) {
        lastErr = e;
        console.log(`❌ [${entry.type}] "${entry.title}" 第${attempt}次失败: ${e.message}`);
        if (attempt < 3) {
          console.log(`  ⏳ ${3}秒后重试...`);
          await new Promise(r => setTimeout(r, 3000));
        }
      }
    }
    if (pushOk) {
      await sendLarkNotification(`【公众号推送通知】${typeLabel}\n✅ 已推送到草稿箱\n标题：${entry.title}\n📅 ${today}（周${dow}）`);
    } else {
      failed++;
      await sendLarkNotification(`【公众号推送通知】${typeLabel}\n❌ 推送失败（重试3次均失败）\n标题：${entry.title}\n原因：${lastErr ? lastErr.message : '未知'}\n📅 ${today}（周${dow}）`);
    }
  }

  console.log(`🎉 今日推送完成 | 平台=${platform} 成功=${pushed} 跳过=${skipped} 失败=${failed}`);
  // 只有实际推送了内容（pushed > 0）或有失败（failed > 0）才发汇总通知
  // 全部跳过时不发，避免 8 次 cron 兜底产生大量无意义汇总
  if (pushed > 0 || failed > 0) {
    await sendLarkNotification(
      `【公众号推送通知】📊 今日推送汇总\n📅 ${today}（周${dow}）\n✅ 成功 ${pushed} 篇\n⏭️ 跳过 ${skipped} 篇\n❌ 失败 ${failed} 篇\n🖥️ 平台：${platform}`
    );
  } else {
    console.log(`📊 全部跳过，不发汇总通知`);
  }
}

// ─── 全局超时：最多执行 5 分钟 ───
const TIMEOUT_MS = 5 * 60 * 1000;
const timer = setTimeout(() => {
  console.error('💥 执行超时（5分钟），强制退出');
  sendLarkNotification('【公众号推送通知】🚨 执行超时（5分钟强制退出）\n请手动检查').then(() => process.exit(1));
}, TIMEOUT_MS);
timer.unref(); // 不阻止进程正常退出

main().catch(e => {
  console.error(`💥 致命错误: ${e.message}`);
  sendLarkNotification(`【公众号推送通知】🚨 脚本致命错误\n原因：${e.message}`).then(() => {
    clearTimeout(timer);
    process.exit(1);
  });
}).then(() => {
  clearTimeout(timer);
});
