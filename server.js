/**
 * WeChat OA Auto-Publisher — CloudStudio 24/7 Service
 *
 * Architecture:
 * - Zero npm dependencies — uses only Node.js built-in modules
 * - Express-free HTTP server for health checks
 * - setInterval-based scheduler (no node-cron needed)
 * - Reads pre-generated content from content/ directory
 * - Publishes to WeChat drafts on schedule
 *
 * Content pipeline:
 * - WorkBuddy (when online) generates content → stores in content/
 * - Re-deploy to CloudStudio to push new content
 * - CloudStudio publishes on schedule regardless of user's PC status
 *
 * Schedule (Beijing time):
 * - 08:45 daily: image post
 * - 08:50 Tue/Thu/Sat: article post
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const wechat = require('./wechat');

const PORT = process.env.PORT || 3000;

// ─── Logging ───
const LOG_FILE = path.join(__dirname, 'publish.log');

function log(msg) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] ${msg}`);
}

function logPublish(entry) {
  const line = JSON.stringify({ ...entry, time: new Date().toISOString() }) + '\n';
  fs.appendFileSync(LOG_FILE, line);
  log(`📤 PUBLISHED: ${entry.type} | ${entry.status} | "${entry.title || ''}"`);
}

// ─── Content loading ───
function loadSchedule() {
  const f = path.join(__dirname, 'content', 'schedule.json');
  if (!fs.existsSync(f)) return [];
  try { return JSON.parse(fs.readFileSync(f, 'utf-8')); }
  catch (e) { log(`⚠️ schedule.json parse error: ${e.message}`); return []; }
}

function loadContent(type, filename) {
  const f = path.join(__dirname, 'content', type === 'article' ? 'articles' : 'images', filename);
  if (!fs.existsSync(f)) return null;
  return fs.readFileSync(f, 'utf-8');
}

// ─── Beijing time helpers ───
function beijingNow() {
  return new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
}

function todayKey() {
  const d = beijingNow();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function isArticleDay() {
  const day = beijingNow().getDay(); // 0=Sun, 2=Tue, 4=Thu, 6=Sat
  return day === 2 || day === 4 || day === 6;
}

// ─── Publishing ───
async function publishArticle(entry) {
  try {
    const token = await wechat.getAccessToken();
    const html = loadContent('article', entry.content_file);
    if (!html) {
      log(`⚠️ Missing article: ${entry.content_file}`);
      return logPublish({ type: 'article', title: entry.title, status: 'SKIP' });
    }

    let thumbId = null;
    if (entry.cover_image) {
      const coverPath = path.join(__dirname, 'content', 'articles', entry.cover_image);
      thumbId = await wechat.uploadThumb(token, coverPath);
    }

    const mediaId = await wechat.createDraft(token, {
      title: entry.title,
      digest: entry.digest || '',
      content_html: html,
      thumb_media_id: thumbId || '',
    });

    logPublish({ type: 'article', title: entry.title, media_id: mediaId, status: 'OK' });
  } catch (e) {
    log(`❌ Article error: ${e.message}`);
    logPublish({ type: 'article', title: entry.title, status: 'FAIL', error: e.message });
  }
}

async function publishImage(entry) {
  try {
    const token = await wechat.getAccessToken();
    const html = loadContent('image', entry.content_file);
    if (!html) {
      log(`⚠️ Missing image: ${entry.content_file}`);
      return logPublish({ type: 'image', title: entry.title, status: 'SKIP' });
    }

    const mediaId = await wechat.createDraft(token, {
      title: entry.title,
      digest: entry.digest || '',
      content_html: html,
      thumb_media_id: '',
    });

    logPublish({ type: 'image', title: entry.title, media_id: mediaId, status: 'OK' });
  } catch (e) {
    log(`❌ Image error: ${e.message}`);
    logPublish({ type: 'image', title: entry.title, status: 'FAIL', error: e.message });
  }
}

// ─── Scheduler (checks every 60 seconds) ───
let lastRun = { image: '', article: '' };

async function tick() {
  const now = beijingNow();
  const key = todayKey();
  const hour = now.getHours();
  const min = now.getMinutes();

  // Image post: 08:45
  if (hour === 8 && min === 45 && lastRun.image !== key) {
    lastRun.image = key;
    log(`⏰ IMAGE POST TRIGGERED for ${key}`);
    const schedule = loadSchedule();
    for (const entry of schedule) {
      if (entry.date === key && entry.type === 'image') {
        await publishImage(entry);
      }
    }
  }

  // Article post: 08:50, only on Tue/Thu/Sat
  if (hour === 8 && min === 50 && isArticleDay() && lastRun.article !== key) {
    lastRun.article = key;
    log(`⏰ ARTICLE POST TRIGGERED for ${key}`);
    const schedule = loadSchedule();
    for (const entry of schedule) {
      if (entry.date === key && entry.type === 'article') {
        await publishArticle(entry);
      }
    }
  }
}

// ─── HTTP Server ───
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === '/') {
    const schedule = loadSchedule();
    const today = todayKey();
    const upcoming = schedule.filter(e => e.date >= today).length;
    const todayCount = schedule.filter(e => e.date === today).length;

    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>公众号自动推送</title>
<style>body{font-family:-apple-system,sans-serif;max-width:700px;margin:40px auto;padding:20px;color:#333}
h1{color:#09b83e}.card{background:#f6f8fa;border-radius:8px;padding:16px;margin:12px 0}
.ok{color:green}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f6f8fa}</style></head>
<body>
<h1>📱 公众号自动推送服务</h1>
<p>状态：<span class="ok">🟢 运行中</span></p>
<div class="card">
<strong>账号</strong>：Zhi-小Xi.我有一个女儿<br>
<strong>作者</strong>：Zylon<br>
<strong>今日</strong>：${today}（${['日','一','二','三','四','五','六'][beijingNow().getDay()]}）<br>
<strong>推送计划</strong>：每日 08:45（贴图）| 周二/四/六 08:50（文章）<br>
<strong>待推送</strong>：${upcoming} 条（今日 ${todayCount} 条）<br>
<strong>服务器</strong>：${new Date().toISOString()}
</div>
<p><a href="/log">📋 推送日志</a> | <a href="/status">🔍 状态</a></p>
</body></html>`);
  }
  else if (url.pathname === '/status') {
    const logLines = fs.existsSync(LOG_FILE)
      ? fs.readFileSync(LOG_FILE, 'utf-8').trim().split('\n').slice(-30).map(l => JSON.parse(l))
      : [];
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ status: 'running', today: todayKey(), lastRuns: lastRun, logs: logLines }, null, 2));
  }
  else if (url.pathname === '/log') {
    const lines = fs.existsSync(LOG_FILE)
      ? fs.readFileSync(LOG_FILE, 'utf-8').trim().split('\n').map(l => JSON.parse(l)).reverse()
      : [];
    const rows = lines.map(e =>
      `<tr><td>${e.time||''}</td><td>${e.type||''}</td><td>${e.status||''}</td><td>${e.title||''}</td></tr>`
    ).join('');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>推送日志</title>
<style>body{font-family:-apple-system,sans-serif;max-width:900px;margin:40px auto;padding:20px}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px}th{background:#f6f8fa}</style></head>
<body><h2>📋 推送日志</h2>
<table><tr><th>时间</th><th>类型</th><th>状态</th><th>标题</th></tr>${rows}</table>
<p><a href="/">← 返回</a></p></body></html>`);
  }
  else {
    res.writeHead(404);
    res.end('Not Found');
  }
});

server.listen(PORT, () => {
  log(`🚀 Server started on port ${PORT}`);
  log(`📅 Today: ${todayKey()} (${['日','一','二','三','四','五','六'][beijingNow().getDay()]})`);
  log(`⏰ Article days: Tue/Thu/Sat | Image: daily`);

  const schedule = loadSchedule();
  log(`📦 Schedule: ${schedule.length} entries loaded`);
  const upcoming = schedule.filter(e => e.date >= todayKey()).slice(0, 10);
  upcoming.forEach(e => log(`  📅 ${e.date} | ${e.type} | "${e.title}"`));
});

// ─── Start scheduler ───
setInterval(tick, 60000);
log('⏱️  Scheduler started (checking every 60s)');
