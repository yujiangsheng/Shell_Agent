#!/usr/bin/env python3
"""OpenShrimp Web 控制台（``web_server``）。

设计要点：

* 零依赖：仅 :mod:`http.server.ThreadingHTTPServer`；前端 HTML/CSS/JS 嵌入为
  常量 :data:`INDEX_HTML`，无需打包。
* 状态集中于 :class:`ServerState`：包括 :class:`OpenShrimp` 智能体、在线计划表
  与四层记忆（详见 :mod:`memory`）。
* 路由集中于 :class:`Handler.do_GET` / :class:`Handler.do_POST`；业务逻辑拆在
  ``_handle_*`` 私有方法中，调用后仅返回 JSON。
* 服务序列化额外提供三个顶层字段以补充 :class:`ShellRequest` 不可序列化的内容：
  ``plan_id``、``script``（由 :func:`render_script` 生成）与 ``force_dry_run``。

API 总览（全部 JSON）：
    GET  /                    静态 HTML
    GET  /api/config          当前 cwd
    GET  /api/models          本地 Ollama 模型列表
    GET  /api/history         近期计划与执行
    GET  /api/habits          高频习惯
    GET  /api/memory          记忆总览·画像·近期经验
    POST /api/plan            生成计划
    POST /api/execute         执行计划
    POST /api/feedback        满意 → 写入长期记忆；不满意 → 重新规划
    POST /api/preference      手动增加偏好 / 规避
    POST /api/learn           根据习惯生成子智能体
    POST /api/pick-dir        macOS 原生目录选择器
    POST /api/shutdown        优雅退出

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from openshrimp import (
    OpenShrimp,
    ShellRequest,
    analyze_history,
    materialize_sub_agent,
    render_script,
)
from memory import (
    WorkingMemory,
    LongTermMemory,
    PersistentMemory,
    ExternalMemory,
    Turn,
    render_memory_block,
)


# ---------- HTML ----------

INDEX_HTML = """<!doctype html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>OpenShrimp 控制台</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --border:#334155; --text:#e2e8f0; --muted:#94a3b8;
          --accent:#38bdf8; --danger:#f87171; --warn:#fbbf24; --ok:#34d399; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); }
  header { display:flex; align-items:center; justify-content:space-between;
           padding: 14px 20px; border-bottom:1px solid var(--border); background: var(--panel); }
  header h1 { margin:0; font-size:18px; }
  .container { display:block; max-width:1100px; margin:0 auto; padding:20px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
  label { font-size:13px; color: var(--muted); }
  select, input, textarea, button {
    background:#0b1220; color:var(--text); border:1px solid var(--border);
    border-radius:6px; padding:8px 10px; font-size:14px;
    max-width:100%; box-sizing:border-box;
  }
  /* 自定义下拉（取代原生 select，避免暗色主题下不可见/不可点） */
  .dd { position:relative; flex:1; min-width:0; }
  .dd-btn { width:100%; text-align:left; background:#0b1220; color:var(--text);
    border:1px solid var(--border); border-radius:6px; padding:8px 28px 8px 10px;
    font-size:14px; cursor:pointer; position:relative; }
  .dd-btn::after { content:"▾"; position:absolute; right:10px; top:50%;
    transform:translateY(-50%); color:var(--muted); font-size:11px; }
  .dd-menu { position:absolute; top:calc(100% + 4px); left:0; right:0;
    background:#0b1220; border:1px solid var(--border); border-radius:6px;
    max-height:240px; overflow-y:auto; z-index:9999; display:none;
    box-shadow:0 8px 24px rgba(0,0,0,0.4); }
  .dd-menu.open { display:block; }
  .dd-item { padding:7px 10px; font-size:13px; color:var(--text); cursor:pointer;
    border-bottom:1px solid #182234; }
  .dd-item:last-child { border-bottom:none; }
  .dd-item:hover, .dd-item.active { background:#1e293b; color:var(--accent); }
  input[type=text], input:not([type]) { min-width:0; }
  .field { display:flex; flex-direction:column; gap:4px; flex:1 1 240px; min-width:0; }
  .field > label { font-weight:600; color:var(--text); }
  .field > input { width:100%; }
  textarea { width:100%; min-height:90px; resize:vertical; font-family: ui-monospace, monospace; }
  .section-title { margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; letter-spacing:.5px; }
  button { cursor:pointer; }
  button.primary { background: var(--accent); color:#001018; border-color: var(--accent); font-weight:600; }
  button.danger { background: var(--danger); color:#220; border-color: var(--danger); font-weight:600; }
  button.ghost { background: transparent; }
  .panel { background: var(--panel); border:1px solid var(--border); border-radius:8px;
           padding:14px; margin-bottom:14px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; margin-left:6px; }
  .badge.read_only { background:#064e3b; color:#a7f3d0; }
  .badge.workspace_write { background:#3f3f46; color:#fde68a; }
  .badge.sensitive { background:#7c2d12; color:#fed7aa; }
  .badge.forbidden { background:#7f1d1d; color:#fecaca; }
  .badge.no_shell { background:#1e3a8a; color:#bfdbfe; }
  pre { background:#0b1220; border:1px solid var(--border); border-radius:6px;
        padding:10px; white-space:pre-wrap; word-break:break-word; max-height:340px; overflow:auto; }
  .muted { color: var(--muted); font-size:12px; }
  .feedback { border-left:4px solid var(--accent); padding:10px 12px; background:#0b1220; border-radius:6px; }
  .feedback.err { border-color: var(--danger); }
  .hidden { display:none; }
  .sidebar h3 { margin:0 0 8px; font-size:14px; color:var(--muted); }
  .hist-item { padding:8px; border:1px solid var(--border); border-radius:6px; margin-bottom:6px;
               cursor:pointer; font-size:12px; }
  .hist-item:hover { border-color: var(--accent); }
  .hist-item .h-meta { color: var(--muted); font-size:11px; margin-top:2px; }
  .toggle { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--muted); }
  /* Chat */
  .chat-panel { padding:0; display:flex; flex-direction:column; height:65vh; }
  #chat { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px; }
  .bubble { max-width:90%; padding:10px 12px; border-radius:12px; font-size:13.5px; line-height:1.55; word-wrap:break-word; }
  .bubble.user { align-self:flex-end; background:#1d4ed8; color:#fff; border-bottom-right-radius:3px; }
  .bubble.agent { align-self:flex-start; background:#0b1220; border:1px solid var(--border); border-bottom-left-radius:3px; width:90%; }
  .bubble.agent .meta { color:var(--muted); font-size:11px; margin-bottom:6px; }
  .bubble pre { margin:6px 0 0; max-height:260px; }
  .bubble .acts { margin-top:10px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .bubble .acts button { padding:4px 10px; font-size:12px; }
  .composer { border-top:1px solid var(--border); padding:10px; display:flex; gap:8px; align-items:flex-end; }
  .composer textarea { flex:1; min-height:46px; resize:vertical; font-family:inherit; }
  .typing { color:var(--muted); font-size:12px; font-style:italic; padding:4px 10px; }
</style>
</head>
<body>
<header>
  <h1>🦐 OpenShrimp 控制台</h1>
  <div>
    <button id=\"btn-shutdown\" class=\"danger\">退出服务</button>
  </div>
</header>

<div class=\"container\">
  <div class=\"panel\">
    <div class=\"row\">
      <div class="field" style="flex:1 1 200px">
        <label for="model">本地 Ollama 模型</label>
        <div style="display:flex; gap:6px">
          <div class="dd" id="dd-model">
            <button type="button" class="dd-btn" id="dd-btn">(加载中…)</button>
            <div class="dd-menu" id="dd-menu"></div>
          </div>
          <input type="hidden" id="model" />
          <button id="btn-refresh-models" class="ghost">刷新</button>
        </div>
      </div>
      <div class="field" style="flex:2 1 320px">
        <label for="cwd">工作目录</label>
        <div style="display:flex; gap:6px">
          <input id="cwd" type="text" style="flex:1" />
          <button id="btn-pick-cwd" class="ghost" title="从资源管理器选择">📁 选择...</button>
        </div>
      </div>
    </div>
    <div class="row" style="margin-top:10px">
      <label class="toggle"><input type="checkbox" id="opt-dry-run" /> 强制 dry-run</label>
      <label class="toggle"><input type="checkbox" id="opt-allow-net" /> 允许联网命令</label>
      <label class="toggle"><input type="checkbox" id="opt-allow-search" /> 🌐 联网检索（DuckDuckGo）</label>
      <button id="btn-memory" class="ghost" style="padding:4px 10px;font-size:12px">🧠 记忆</button>
      <span id="status" class="muted" style="margin-left:auto"></span>
    </div>
  </div>

  <div class="panel chat-panel">
    <div id="chat"></div>
    <div class="composer">
      <textarea id="composer" rows="2" placeholder="例如：列出最近三天的图片文件…（Enter 发送，Shift+Enter 换行）"></textarea>
      <button id="btn-send" class="primary">发送</button>
    </div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);

// ----- API helper -----
async function api(path, body) {
  const opts = { method: body ? 'POST' : 'GET',
                 headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// ----- 对话渲染 -----
const chat = $('chat');
const sessionId = 'sess-' + Math.random().toString(36).slice(2, 12);
// convo 与 chat 中的气泡顺序一一对应（仅记录有意义的轮次）
const convo = [];     // [{role,kind,text, bubbleEl?}]

function scrollChat() { chat.scrollTop = chat.scrollHeight; }

function addUserBubble(text, opts={}) {
  const idx = convo.length;
  const div = document.createElement('div');
  div.className = 'bubble user';
  div.dataset.idx = String(idx);

  const textSpan = document.createElement('div');
  textSpan.className = 'u-text';
  textSpan.style.whiteSpace = 'pre-wrap';
  textSpan.textContent = text;
  div.appendChild(textSpan);

  const editBtn = document.createElement('button');
  editBtn.className = 'ghost u-edit';
  editBtn.title = '编辑这条消息并重做';
  editBtn.textContent = '✏️';
  editBtn.style.cssText = 'margin-top:6px;padding:2px 8px;font-size:11px;float:right';
  editBtn.onclick = () => beginEditUserBubble(div);
  div.appendChild(editBtn);

  chat.appendChild(div);
  convo.push({ role: 'user', kind: 'instruction', text, bubbleEl: div });
  scrollChat();
  return div;
}

function beginEditUserBubble(div) {
  const idx = parseInt(div.dataset.idx, 10);
  const turn = convo[idx];
  if (!turn) return;
  div.innerHTML = '';
  const ta = document.createElement('textarea');
  ta.value = turn.text;
  ta.style.cssText = 'width:100%;min-height:64px;background:#0b1220;color:#fff;border:1px solid #38bdf8;border-radius:6px;padding:6px;font-family:inherit;font-size:13px';
  div.appendChild(ta);
  const acts = document.createElement('div');
  acts.style.cssText = 'margin-top:6px;display:flex;gap:6px;justify-content:flex-end';
  const bSave = document.createElement('button');
  bSave.className = 'primary';
  bSave.textContent = '保存并重做';
  bSave.style.cssText = 'padding:4px 10px;font-size:12px';
  const bCancel = document.createElement('button');
  bCancel.className = 'ghost';
  bCancel.textContent = '取消';
  bCancel.style.cssText = 'padding:4px 10px;font-size:12px';
  acts.appendChild(bSave); acts.appendChild(bCancel);
  div.appendChild(acts);
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);

  bCancel.onclick = () => restoreUserBubble(div, turn.text);
  bSave.onclick = () => {
    const newText = ta.value.trim();
    if (!newText) { alert('内容不能为空'); return; }
    truncateAfter(idx);     // 删除该用户气泡之后的所有气泡 + convo 项
    turn.text = newText;
    restoreUserBubble(div, newText);
    sendInstruction(newText);  // 重新走规划
  };
}

function restoreUserBubble(div, text) {
  div.innerHTML = '';
  const textSpan = document.createElement('div');
  textSpan.className = 'u-text';
  textSpan.style.whiteSpace = 'pre-wrap';
  textSpan.textContent = text;
  div.appendChild(textSpan);
  const editBtn = document.createElement('button');
  editBtn.className = 'ghost u-edit';
  editBtn.textContent = '✏️';
  editBtn.title = '编辑这条消息并重做';
  editBtn.style.cssText = 'margin-top:6px;padding:2px 8px;font-size:11px;float:right';
  editBtn.onclick = () => beginEditUserBubble(div);
  div.appendChild(editBtn);
}

function truncateAfter(idx) {
  // 删除 convo 中 idx 之后（不含 idx）的项及其 DOM 气泡
  while (convo.length > idx + 1) {
    const t = convo.pop();
    if (t && t.bubbleEl && t.bubbleEl.parentNode) t.bubbleEl.parentNode.removeChild(t.bubbleEl);
  }
  // 但用户编辑后我们要把这个 idx 自身也要替换，所以下游会改 turn.text，不需要在这里删 idx
  // 同时清掉编辑气泡之后可能残留的 typing 节点
  Array.from(chat.querySelectorAll('.typing')).forEach(n => n.remove());
}

function addAgentBubble(builder, opts={}) {
  const div = document.createElement('div');
  div.className = 'bubble agent';
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = '🦐 OpenShrimp · ' + new Date().toLocaleTimeString();
  div.appendChild(meta);
  const body = document.createElement('div');
  div.appendChild(body);
  if (typeof builder === 'string') body.innerHTML = builder;
  else builder(body, div);
  chat.appendChild(div);
  // 记入 convo（用纯文本摘要，给后续 LLM 看）
  let summary = opts.summary;
  if (summary === undefined) summary = (typeof builder === 'string') ? body.textContent : body.textContent;
  convo.push({ role: 'agent', kind: opts.kind || 'note',
               text: (summary || '').slice(0, 400), bubbleEl: div });
  scrollChat();
  return { wrapper: div, body };
}

function addTyping(text) {
  const div = document.createElement('div');
  div.className = 'typing';
  div.textContent = text || '思考中…';
  chat.appendChild(div);
  scrollChat();
  return div;
}

// ----- 加载模型 / 配置 -----
function setModel(name) {
  $('model').value = name || '';
  $('dd-btn').textContent = name || '(未选择)';
  Array.from($('dd-menu').querySelectorAll('.dd-item')).forEach(el => {
    el.classList.toggle('active', el.dataset.value === name);
  });
}
function openMenu(open) {
  $('dd-menu').classList.toggle('open', !!open);
}
$('dd-btn').onclick = (ev) => {
  ev.stopPropagation();
  openMenu(!$('dd-menu').classList.contains('open'));
};
document.addEventListener('click', () => openMenu(false));

async function loadModels() {
  try {
    const data = await api('/api/models');
    const menu = $('dd-menu');
    menu.innerHTML = '';
    if (!data.models || data.models.length === 0) {
      $('dd-btn').textContent = '(未检测到本地模型，请先 ollama pull)';
      $('model').value = '';
    } else {
      for (const m of data.models) {
        const it = document.createElement('div');
        it.className = 'dd-item';
        it.dataset.value = m;
        it.textContent = m;
        it.onclick = (ev) => { ev.stopPropagation(); setModel(m); openMenu(false); };
        menu.appendChild(it);
      }
      // 默认选中第一项
      if (!$('model').value) setModel(data.models[0]);
    }
    $('status').textContent = '';
  } catch (e) {
    $('status').textContent = '无法连接 Ollama：' + e.message;
    $('dd-btn').textContent = '(连接 Ollama 失败)';
  }
}
async function loadConfig() {
  const data = await api('/api/config');
  $('cwd').value = data.cwd;
}

$('btn-refresh-models').onclick = loadModels;
$('btn-pick-cwd').onclick = async () => {
  const cur = $('cwd').value || '';
  $('btn-pick-cwd').disabled = true;
  try {
    const r = await api('/api/pick-dir', { initial: cur });
    if (r && r.path) $('cwd').value = r.path;
  } catch (e) { alert('选择目录失败：' + e.message); }
  finally { $('btn-pick-cwd').disabled = false; }
};

// ----- 计划气泡：含命令/脚本切换、执行、取消、复制、敏感确认 -----
function renderPlanBubble(plan) {
  const summary = '【计划】' + (plan.purpose || '') + ' | 风险=' + plan.risk_level
                  + ' | 命令=' + ((plan.command || '').split('\\n')[0]).slice(0, 200);
  return addAgentBubble((body) => {
    const riskClass = 'badge ' + plan.risk_level;
    body.innerHTML =
      '<div><b>📋 我打算这样做</b> <span class="' + riskClass + '">' + escapeHtml(plan.risk_level) + '</span></div>' +
      '<div class="muted" style="margin-top:4px">目的：' + escapeHtml(plan.purpose || '') + '</div>' +
      (plan.explanation
        ? '<div style="margin:8px 0;padding:8px 10px;background:#0f172a;border-left:3px solid var(--ok);border-radius:4px;font-size:12.5px;line-height:1.6">📖 ' + escapeHtml(plan.explanation) + '</div>'
        : '') +
      '<div class="row" style="gap:6px;margin:6px 0 4px">' +
        '<button class="ghost t-cmd" style="padding:4px 10px;font-size:12px">命令</button>' +
        '<button class="ghost t-script" style="padding:4px 10px;font-size:12px">完整脚本</button>' +
        '<button class="ghost b-copy" style="padding:4px 10px;font-size:12px;margin-left:auto">复制</button>' +
      '</div>' +
      '<pre class="code"></pre>' +
      (plan.risk_level === 'sensitive'
        ? '<div class="confirm-block" style="margin-top:8px">' +
            '<div class="muted">⚠️ 该操作敏感，请逐字键入下面的确认语句：</div>' +
            '<pre style="margin:6px 0">' + escapeHtml(plan.confirmation_text || ('确认执行：' + (plan.purpose||''))) + '</pre>' +
            '<input type="text" class="confirm-input" style="width:100%" placeholder="键入确认语句" />' +
          '</div>'
        : '') +
      '<div class="acts">' +
        '<button class="primary b-exec">▶ 执行</button>' +
        '<button class="ghost b-cancel">取消</button>' +
      '</div>';

    let view = 'cmd';
    const codeEl = body.querySelector('.code');
    const tCmd = body.querySelector('.t-cmd');
    const tScript = body.querySelector('.t-script');
    function render() {
      const txt = (view === 'script' && plan.script) ? plan.script : (plan.command || '(无 Shell 命令)');
      codeEl.textContent = txt;
      tCmd.style.opacity = view === 'cmd' ? '1' : '0.6';
      tScript.style.opacity = view === 'script' ? '1' : '0.6';
    }
    tCmd.onclick = () => { view = 'cmd'; render(); };
    tScript.onclick = () => { view = 'script'; render(); };
    body.querySelector('.b-copy').onclick = async (ev) => {
      const txt = (view === 'script' && plan.script) ? plan.script : (plan.command || '');
      try { await navigator.clipboard.writeText(txt); ev.target.textContent = '已复制'; setTimeout(()=>ev.target.textContent='复制',1200);}
      catch(e){ alert('复制失败：'+e.message); }
    };
    render();

    const bExec = body.querySelector('.b-exec');
    const bCancel = body.querySelector('.b-cancel');
    if (plan.risk_level === 'forbidden') bExec.disabled = true;
    bCancel.onclick = () => {
      bExec.disabled = true; bCancel.disabled = true;
      addAgentBubble('已取消该计划。');
    };
    bExec.onclick = async () => {
      let confirmation = null;
      if (plan.risk_level === 'sensitive') {
        const inp = body.querySelector('.confirm-input');
        confirmation = inp ? inp.value : '';
        if (!confirmation) { alert('请先输入确认语句'); return; }
      }
      bExec.disabled = true; bCancel.disabled = true;
      const t = addTyping('执行中…');
      try {
        const res = await api('/api/execute', { plan_id: plan.plan_id, confirmation });
        t.remove();
        renderResultBubble(plan, res);
      } catch (e) {
        t.remove();
        addAgentBubble('❌ 执行失败：' + escapeHtml(e.message), { kind: 'result', summary: '执行失败：' + e.message });
        bExec.disabled = false; bCancel.disabled = false;
      }
    };
  }, { kind: 'plan', summary: summary });
}

function renderResultBubble(plan, res) {
  const ok = res.exit_code === 0;
  const summary = '【结果】退出码=' + res.exit_code + ' | ' + (res.feedback || '').slice(0, 200);
  return addAgentBubble((body) => {
    body.innerHTML =
      '<div><b>' + (ok ? '✅ 已执行' : '⚠️ 执行结束') + '</b> <span class="muted">退出码 ' + res.exit_code + '</span></div>' +
      '<div class="feedback' + (ok ? '' : ' err') + '" style="margin-top:6px">' + escapeHtml(res.feedback || '') + '</div>' +
      '<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">标准输出</summary><pre>' + escapeHtml(res.stdout || '(空)') + '</pre></details>' +
      '<details style="margin-top:6px"><summary class="muted" style="cursor:pointer">标准错误</summary><pre>' + escapeHtml(res.stderr || '(空)') + '</pre></details>' +
      '<div style="margin-top:10px;padding:10px;border:1px dashed var(--border);border-radius:6px">' +
        '<div style="font-weight:600;margin-bottom:6px">这次任务你满意吗？</div>' +
        '<div class="acts" style="margin:0 0 6px">' +
          '<button class="primary b-yes">👍 满意</button>' +
          '<button class="danger b-no">👎 不满意</button>' +
          '<span class="muted r-status"></span>' +
        '</div>' +
        '<input type="text" class="r-comment" style="width:100%" placeholder="可选：留下备注（哪里不对、希望怎么改）" />' +
      '</div>';
    const bYes = body.querySelector('.b-yes');
    const bNo = body.querySelector('.b-no');
    const status = body.querySelector('.r-status');
    async function send(satisfied) {
      bYes.disabled = true; bNo.disabled = true;
      status.textContent = satisfied ? '正在记录经验…' : '正在重新规划…';
      try {
        const r = await api('/api/feedback', {
          plan_id: plan.plan_id,
          satisfied,
          comment: body.querySelector('.r-comment').value || ''
        });
        status.textContent = '';
        if (r.action === 'replan' && r.replan) {
          addAgentBubble('🔄 ' + escapeHtml(r.message || '已根据反馈重新规划，请审阅新计划：'),
                         { kind: 'feedback', summary: '已根据不满意反馈重新规划' });
          renderPlanBubble(r.replan);
        } else if (r.action === 'accumulated') {
          addAgentBubble('✅ ' + escapeHtml(r.message || '已记录这次成功经验'),
                         { kind: 'feedback', summary: '已记录满意经验' });
        } else {
          addAgentBubble(escapeHtml(r.message || '已记录反馈'),
                         { kind: 'feedback', summary: r.message || '已记录反馈' });
        }
      } catch (e) {
        status.textContent = '反馈失败：' + e.message;
        bYes.disabled = false; bNo.disabled = false;
      }
    }
    bYes.onclick = () => send(true);
    bNo.onclick = () => send(false);
  }, { kind: 'result', summary: summary });
}

// ----- 发送指令 -----
async function sendInstruction(presetText) {
  let text;
  if (typeof presetText === 'string') {
    text = presetText.trim();
  } else {
    const ta = $('composer');
    text = ta.value.trim();
    if (!text) return;
    ta.value = '';
    addUserBubble(text);
  }
  if (!text) return;
  const t = addTyping('规划中…');
  // 把 convo 中所有有内容的轮次（不带 DOM 引用）发给后端
  const conversation = convo.map(c => ({ role: c.role, kind: c.kind, text: c.text }));
  try {
    const data = await api('/api/plan', {
      query: text,
      session_id: sessionId,
      conversation,
      model: $('model').value, cwd: $('cwd').value,
      policy_overrides: {
        force_dry_run: $('opt-dry-run').checked,
        allow_network: $('opt-allow-net').checked,
        allow_search: $('opt-allow-search').checked,
      }
    });
    t.remove();
    renderPlanBubble(data);
  } catch (e) {
    t.remove();
    addAgentBubble('❌ 规划失败：' + escapeHtml(e.message), { summary: '规划失败' });
  }
}
$('btn-send').onclick = sendInstruction;
$('composer').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    sendInstruction();
  }
});

// ----- 退出服务 -----
$('btn-shutdown').onclick = async () => {
  if (!confirm('确定要退出 OpenShrimp 服务吗？')) return;
  try { await api('/api/shutdown', {}); } catch (e) {}
  document.body.innerHTML = '<div style="padding:40px;text-align:center;color:#94a3b8">' +
    '服务已退出，可以关闭此页面。</div>';
};

// ----- 记忆面板 -----
$('btn-memory').onclick = async () => {
  try {
    const m = await api('/api/memory');
    const prefs = (m.profile && m.profile.preferences) || [];
    const avoid = (m.profile && m.profile.avoid) || [];
    let html = '<div><b>🧠 记忆总览</b></div>';
    html += '<div class="muted" style="margin-top:6px">长期记忆（成功经验）：' + m.episode_count + ' 条</div>';
    html += '<div class="muted">本地笔记：' + m.notes_count + ' 篇 · 目录 ' + escapeHtml(m.notes_dir) + '</div>';
    html += '<div style="margin-top:8px"><b>持久偏好</b></div>';
    html += prefs.length ? ('<ul style="margin:4px 0 0 18px">' + prefs.map(p => '<li>' + escapeHtml(p) + '</li>').join('') + '</ul>')
                         : '<div class="muted">暂无</div>';
    html += '<div style="margin-top:6px"><b>规避事项</b></div>';
    html += avoid.length ? ('<ul style="margin:4px 0 0 18px">' + avoid.map(p => '<li>' + escapeHtml(p) + '</li>').join('') + '</ul>')
                         : '<div class="muted">暂无</div>';
    html += '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' +
              '<input type="text" class="m-pref" placeholder="新增偏好，例如：默认请用 dry-run" style="flex:1;min-width:220px" />' +
              '<button class="primary m-add-pref" style="padding:4px 10px;font-size:12px">＋偏好</button>' +
              '<button class="ghost m-add-avoid" style="padding:4px 10px;font-size:12px">＋规避</button>' +
            '</div>';
    if ((m.recent_episodes || []).length) {
      html += '<div style="margin-top:10px"><b>最近经验</b></div>';
      html += '<ul style="margin:4px 0 0 18px">';
      for (const ep of m.recent_episodes) {
        html += '<li>' + escapeHtml(ep.purpose || '') + ' <span class="muted">' + escapeHtml(ep.ts || '') + '</span></li>';
      }
      html += '</ul>';
    }
    const ref = addAgentBubble(html, { kind: 'note', summary: '展示了记忆总览' });
    const inp = ref.body.querySelector('.m-pref');
    ref.body.querySelector('.m-add-pref').onclick = async () => {
      if (!inp.value.trim()) return;
      await api('/api/preference', { kind: 'preference', text: inp.value.trim() });
      inp.value = ''; addAgentBubble('已记录新的偏好。', { kind: 'note', summary: '更新偏好' });
    };
    ref.body.querySelector('.m-add-avoid').onclick = async () => {
      if (!inp.value.trim()) return;
      await api('/api/preference', { kind: 'avoid', text: inp.value.trim() });
      inp.value = ''; addAgentBubble('已记录新的规避事项。', { kind: 'note', summary: '更新规避' });
    };
  } catch (e) {
    addAgentBubble('读取记忆失败：' + escapeHtml(e.message));
  }
};

// ----- 启动 -----
loadModels();
loadConfig();
addAgentBubble(
  '你好！我是 🦐 OpenShrimp。请把你想完成的任务用中文告诉我，例如：<br>' +
  '<span class="muted">「列出最近三天的图片文件」「把当前目录所有 .jpeg 重命名为 .jpg」</span><br><br>' +
  '小贴士：<br>' +
  '· 任意一条历史指令右下角的 ✏️ 可<b>编辑后重做</b>，对话上下文会一并更新；<br>' +
  '· 顶部 🌐 联网检索 开启后，我会调用 DuckDuckGo 摘要补充上下文；<br>' +
  '· 点 🧠 记忆 可以查看/添加我对你的持久偏好。',
  { kind: 'note', summary: '欢迎语：介绍编辑、联网、记忆功能' }
);
</script>
</body>
</html>
"""


# ---------- Server state ----------

class ServerState:
    """跨请求的进程状态。

    查询 ``self.plans[plan_id]`` 可拿到一条计划上下文：``req`` (ShellRequest)、
    ``cwd``、``query``、``overrides``（本请求临时覆盖的策略）、``session_id``、
    ``executed`` 与 ``force_dry_run`` 标记。:py:meth:`Handler._handle_feedback`
    需要这些上下文以应用同一策略重新规划。
    """

    def __init__(self, agent: OpenShrimp, ollama_host: str, default_cwd: Path):
        self.agent = agent
        self.ollama_host = ollama_host.rstrip("/")
        self.default_cwd = default_cwd
        self.plans: dict[str, dict] = {}
        self.lock = threading.Lock()
        # 四层记忆
        memory_root = (default_cwd / ".openshrimp").resolve()
        self.working_memory = WorkingMemory()
        self.long_term_memory = LongTermMemory(memory_root / "episodes.jsonl")
        self.persistent_memory = PersistentMemory(memory_root / "profile.json")
        self.external_memory = ExternalMemory(memory_root / "notes")

    def list_models(self) -> list[str]:
        """查询 Ollama ``/api/tags`` 的本地模型名列表；连接失败返回空列表。"""
        try:
            with urllib.request.urlopen(f"{self.ollama_host}/api/tags", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []

    def read_history(self, limit: int = 20) -> list[dict]:
        log_path = self.agent.audit_log_path
        if not log_path.exists():
            return []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        items: list[dict] = []
        # Pair plan + executed records by walking from the end
        last_plan: Optional[dict] = None
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = rec.get("event")
            if event == "executed":
                items.append({
                    "ts": rec.get("ts"),
                    "command": rec.get("command", ""),
                    "exit_code": rec.get("exit_code"),
                    "purpose": "",
                })
            elif event == "plan":
                req = rec.get("request", {}) or {}
                purpose = req.get("purpose", "")
                cmd = req.get("command", "")
                # Attach purpose to the most recent matching executed item
                attached = False
                for it in items:
                    if not it["purpose"] and it["command"] == cmd:
                        it["purpose"] = purpose
                        attached = True
                        break
                if not attached:
                    items.append({
                        "ts": rec.get("ts"),
                        "command": cmd,
                        "exit_code": None,
                        "purpose": purpose,
                    })
            if len(items) >= limit:
                break
        return items[:limit]


def _make_feedback(req: ShellRequest, exit_code: int, stdout: str, stderr: str) -> str:
    """拼接一句人读的执行反馈。该反馈会作为气泡文本展示在 Web UI 中。"""
    n_out = len([l for l in stdout.splitlines() if l.strip()])
    status = "成功" if exit_code == 0 else f"失败（退出码 {exit_code}）"
    parts = [f"任务{status}：{req.purpose}。"]
    if n_out > 0:
        parts.append(f"产生 {n_out} 行有效输出。")
    elif exit_code == 0:
        parts.append("命令执行完毕，无输出。")
    if stderr.strip():
        first_err = stderr.strip().splitlines()[0][:200]
        parts.append(f"错误信息：{first_err}")
    return " ".join(parts)


# ---------- HTTP handler ----------

class Handler(BaseHTTPRequestHandler):
    state: ServerState  # injected on class

    def log_message(self, fmt, *args):  # quieter logging
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # GET
    def do_GET(self):  # noqa: N802
        if self.path == "/" or self.path == "/index.html":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/models":
            return self._send_json(200, {"models": self.state.list_models()})
        if self.path == "/api/config":
            return self._send_json(200, {"cwd": str(self.state.default_cwd)})
        if self.path == "/api/history":
            return self._send_json(200, {"items": self.state.read_history(limit=20)})
        if self.path == "/api/habits":
            return self._send_json(200, {
                "items": analyze_history(self.state.agent.audit_log_path, min_count=3)
            })
        if self.path == "/api/memory":
            st = self.state
            episodes = st.long_term_memory.all()
            note_count = sum(1 for _ in st.external_memory._iter_note_files())
            return self._send_json(200, {
                "profile": st.persistent_memory.load(),
                "episode_count": len(episodes),
                "recent_episodes": episodes[-5:],
                "notes_dir": str(st.external_memory.notes_dir),
                "notes_count": note_count,
            })
        self._send_json(404, {"error": "not found"})

    # POST
    def do_POST(self):  # noqa: N802
        if self.path == "/api/plan":
            return self._handle_plan()
        if self.path == "/api/execute":
            return self._handle_execute()
        if self.path == "/api/learn":
            return self._handle_learn()
        if self.path == "/api/feedback":
            return self._handle_feedback()
        if self.path == "/api/pick-dir":
            return self._handle_pick_dir()
        if self.path == "/api/preference":
            return self._handle_preference()
        if self.path == "/api/shutdown":
            return self._handle_shutdown()
        self._send_json(404, {"error": "not found"})

    def _build_memory_block(self, query: str, allow_search: bool) -> str:
        st = self.state
        persistent = st.persistent_memory.load()
        episodes = st.long_term_memory.search(query, top_k=3)
        notes = st.external_memory.search_notes(query, top_k=2)
        web = st.external_memory.web_search(query) if allow_search else None
        return render_memory_block(
            persistent=persistent, episodes=episodes, notes=notes, web=web,
        )

    def _handle_plan(self):
        body = self._read_json()
        query = (body.get("query") or "").strip()
        model = (body.get("model") or "").strip()
        cwd = Path((body.get("cwd") or str(self.state.default_cwd))).expanduser()
        session_id = (body.get("session_id") or "default").strip() or "default"
        conversation = body.get("conversation")
        if not isinstance(conversation, list):
            conversation = None
        if not query:
            return self._send_json(400, {"error": "query 为空"})
        if not cwd.is_dir():
            return self._send_json(400, {"error": f"cwd 不存在或不是目录: {cwd}"})

        agent = self.state.agent
        if model:
            agent.model = model
            agent.use_ollama = True

        # 工作记忆：用前端的会话快照覆盖（支持编辑历史）
        if conversation is not None:
            self.state.working_memory.replace(session_id, conversation)
        convo_dicts = [t.to_dict() for t in self.state.working_memory.get(session_id)]

        # Apply per-request policy overrides without persisting them
        overrides = body.get("policy_overrides") or {}
        allow_search = bool(overrides.get("allow_search")) if isinstance(overrides, dict) else False
        memory_block = self._build_memory_block(query, allow_search)

        original_policy = dict(agent.policy)
        try:
            if isinstance(overrides, dict):
                if "force_dry_run" in overrides:
                    agent.policy["force_dry_run"] = bool(overrides["force_dry_run"])
                if "allow_network" in overrides:
                    agent.policy["allow_network"] = bool(overrides["allow_network"])
            try:
                req = agent.plan(query, cwd,
                                 conversation=convo_dicts or None,
                                 memory_block=memory_block or None)
            except Exception as e:  # noqa: BLE001
                return self._send_json(500, {"error": f"规划失败: {e}"})

            # Normalize risk through static analysis to be consistent with execute()
            static = agent.static_risk_analyze(req.command)
            final_risk = agent._max_risk(req.risk_level, static)
            req.risk_level = final_risk
            if final_risk == "sensitive" and not req.confirmation_text:
                req.confirmation_text = f"确认执行：{req.purpose}"
            # If policy forces dry-run, surface it to the UI as a no-op execution path
            req_force_dry_run = bool(agent.policy.get("force_dry_run", False))
        finally:
            agent.policy = original_policy

        plan_id = uuid.uuid4().hex
        with self.state.lock:
            self.state.plans[plan_id] = {
                "req": req,
                "force_dry_run": req_force_dry_run,
                "query": query,
                "model": model,
                "cwd": str(cwd),
                "overrides": overrides if isinstance(overrides, dict) else {},
                "session_id": session_id,
                "executed": False,
            }
        out = asdict(req)
        out["plan_id"] = plan_id
        out["force_dry_run"] = req_force_dry_run
        out["script"] = render_script(req)
        out["memory_used"] = bool(memory_block)
        self._send_json(200, out)

    def _handle_execute(self):
        body = self._read_json()
        plan_id = body.get("plan_id")
        confirmation = body.get("confirmation")
        with self.state.lock:
            entry = self.state.plans.get(plan_id) if plan_id else None
        if entry is None:
            return self._send_json(400, {"error": "plan_id 无效或已过期"})
        req = entry["req"]
        force_dry_run = entry["force_dry_run"]
        entry["executed"] = True

        if force_dry_run:
            return self._send_json(200, {
                "exit_code": 0, "stdout": "", "stderr": "",
                "feedback": f"策略强制 dry-run：{req.purpose}。未执行命令。",
            })

        agent = self.state.agent
        # Re-check risk
        final_risk = agent._max_risk(req.risk_level, agent.static_risk_analyze(req.command))
        if final_risk == "forbidden":
            agent._audit("blocked", {"reason": "forbidden", "command": req.command})
            return self._send_json(200, {
                "exit_code": 2,
                "stdout": "",
                "stderr": "该命令被安全策略拒绝",
                "feedback": "操作被拒绝：命令命中 forbidden 策略。",
            })
        if final_risk == "no_shell" or not req.command:
            return self._send_json(200, {
                "exit_code": 0, "stdout": "", "stderr": "",
                "feedback": "本次请求无需执行 Shell。",
            })
        if final_risk == "sensitive":
            expected = req.confirmation_text or "确认执行敏感操作"
            if (confirmation or "").strip() != expected:
                return self._send_json(200, {
                    "exit_code": 3, "stdout": "", "stderr": "",
                    "feedback": "已取消：确认语句不匹配。",
                })

        # Execute (replicates _run_shell logic to capture output for the API)
        max_to = int(agent.policy.get("max_timeout_seconds", 300))
        timeout = min(req.timeout_seconds, max_to)
        try:
            result = subprocess.run(
                ["bash", "-lc", req.command] if req.interpreter == "bash"
                else [req.interpreter, "-lc", req.command],
                cwd=req.cwd, capture_output=True, text=True, timeout=timeout,
            )
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            stdout, stderr, code = "", f"命令超时（>{timeout}s）", 124

        agent._audit("executed", {
            "command": req.command, "cwd": req.cwd,
            "interpreter": req.interpreter, "exit_code": code,
            "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
        })

        feedback = _make_feedback(req, code, stdout, stderr)
        # Truncate large outputs for the UI
        max_chars = 20000
        if len(stdout) > max_chars:
            stdout = stdout[:max_chars] + "\n...<TRUNCATED>..."
        if len(stderr) > max_chars:
            stderr = stderr[:max_chars] + "\n...<TRUNCATED>..."
        self._send_json(200, {
            "exit_code": code, "stdout": stdout, "stderr": stderr, "feedback": feedback,
        })

    def _handle_shutdown(self):
        self._send_json(200, {"ok": True})
        # Schedule shutdown after response is flushed
        threading.Thread(target=_delayed_shutdown, args=(self.server,), daemon=True).start()

    def _handle_pick_dir(self):
        body = self._read_json() or {}
        initial = (body.get("initial") or str(self.state.default_cwd)).strip()
        # 仅 macOS 可用：调用 osascript 弹出系统文件夹选择器
        if sys.platform != "darwin":
            return self._send_json(400, {"error": "目录选择器仅在 macOS 上可用；请手动输入路径。"})
        try:
            init_path = Path(initial).expanduser().resolve()
        except Exception:
            init_path = Path.home()
        if not init_path.is_dir():
            init_path = Path.home()
        # AppleScript: choose folder with prompt and default location
        script = (
            'try\n'
            '  set theFolder to choose folder with prompt "选择工作目录" '
            f'default location (POSIX file "{str(init_path)}")\n'
            '  return POSIX path of theFolder\n'
            'on error number -128\n'
            '  return "__CANCELLED__"\n'
            'end try'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return self._send_json(500, {"error": f"无法调用 osascript: {e}"})
        if result.returncode != 0:
            return self._send_json(500, {"error": result.stderr.strip() or "osascript 失败"})
        path = result.stdout.strip().rstrip("/")
        if path == "__CANCELLED__" or not path:
            return self._send_json(200, {"cancelled": True})
        return self._send_json(200, {"path": path})

    def _handle_feedback(self):
        body = self._read_json()
        plan_id = (body.get("plan_id") or "").strip()
        satisfied = bool(body.get("satisfied"))
        comment = (body.get("comment") or "").strip()
        if not plan_id:
            return self._send_json(400, {"error": "plan_id 缺失"})
        with self.state.lock:
            entry = self.state.plans.get(plan_id)
        agent = self.state.agent
        payload = {
            "plan_id": plan_id,
            "satisfied": satisfied,
            "comment": comment,
        }
        if entry is not None:
            req = entry["req"]
            payload["purpose"] = req.purpose
            payload["command"] = req.command
            payload["risk_level"] = req.risk_level
        agent._audit("feedback", payload)

        # 满意：积累经验，鼓励生成习惯子智能体
        if satisfied:
            if entry is not None:
                req = entry["req"]
                agent._audit("success", {
                    "plan_id": plan_id,
                    "purpose": req.purpose,
                    "command": req.command,
                    "interpreter": req.interpreter,
                    "risk_level": req.risk_level,
                    "explanation": req.explanation,
                    "cwd": req.cwd,
                })
                # 长期记忆：写入情景
                self.state.long_term_memory.add_episode(
                    purpose=req.purpose,
                    command=req.command,
                    risk_level=req.risk_level,
                    cwd=req.cwd,
                    comment=comment,
                )
            # 持久记忆：从备注里抽取偏好/规避
            pref, avoid = PersistentMemory.auto_extract(comment)
            if pref:
                self.state.persistent_memory.add_preference(pref)
            if avoid:
                self.state.persistent_memory.add_avoidance(avoid)
            habits = analyze_history(agent.audit_log_path, min_count=3)
            promote = None
            if entry is not None:
                from openshrimp import _normalize_purpose
                key = _normalize_purpose(entry["req"].purpose)
                promote = next((h for h in habits if h["key"] == key and h["count"] >= 3), None)
            return self._send_json(200, {
                "ok": True,
                "action": "accumulated",
                "message": "太好了！已把这次成功经验写入长期记忆。" + (
                    f"\n\n这个指令已重复 {promote['count']} 次，建议在「我的习惯」中生成专用子智能体，以后一键调用。"
                    if promote else ""
                ),
                "promote_habit_key": promote["key"] if promote else None,
            })

        # 不满意：重新规划
        if entry is None:
            return self._send_json(200, {
                "ok": True,
                "action": "noop",
                "message": "已记录不满意，但原计划已过期、无法重做。请重新提交指令。",
            })
        cwd = Path(entry["cwd"]).expanduser()
        augmented = entry["query"]
        if comment:
            augmented += f"\n\n[用户反馈] 上一版不满意，备注：{comment}\n请修正后重新生成。"
        else:
            augmented += "\n\n[用户反馈] 上一版不满意，请提供不同的实现方案或更低风险的替代。"

        # 工作记忆 + 记忆上下文
        sid = entry.get("session_id", "default")
        convo_dicts = [t.to_dict() for t in self.state.working_memory.get(sid)]
        # 把这条反馈追加到对话历史，让 LLM 看到“用户不满意 + 备注”
        convo_dicts.append({
            "role": "user", "kind": "feedback",
            "text": (comment or "我对上一个方案不满意，请重做。"),
        })
        overrides = entry.get("overrides") or {}
        allow_search = bool(overrides.get("allow_search"))
        memory_block = self._build_memory_block(augmented, allow_search)

        # 应用原计划的策略覆盖
        original_policy = dict(agent.policy)
        try:
            if overrides.get("force_dry_run") is not None:
                agent.policy["force_dry_run"] = bool(overrides["force_dry_run"])
            if overrides.get("allow_network") is not None:
                agent.policy["allow_network"] = bool(overrides["allow_network"])
            try:
                new_req = agent.plan(augmented, cwd,
                                     conversation=convo_dicts or None,
                                     memory_block=memory_block or None)
            except Exception as e:  # noqa: BLE001
                return self._send_json(500, {"error": f"重新规划失败: {e}"})
            static = agent.static_risk_analyze(new_req.command)
            new_req.risk_level = agent._max_risk(new_req.risk_level, static)
            if new_req.risk_level == "sensitive" and not new_req.confirmation_text:
                new_req.confirmation_text = f"确认执行：{new_req.purpose}"
            new_force_dry = bool(agent.policy.get("force_dry_run", False))
        finally:
            agent.policy = original_policy

        new_plan_id = uuid.uuid4().hex
        with self.state.lock:
            self.state.plans[new_plan_id] = {
                "req": new_req,
                "force_dry_run": new_force_dry,
                "query": entry["query"],
                "model": entry.get("model", ""),
                "cwd": str(cwd),
                "overrides": overrides,
                "session_id": sid,
                "executed": False,
            }
        plan_dict = asdict(new_req)
        plan_dict["plan_id"] = new_plan_id
        plan_dict["force_dry_run"] = new_force_dry
        plan_dict["script"] = render_script(new_req)
        return self._send_json(200, {
            "ok": True,
            "action": "replan",
            "message": "收到不满意反馈，已重新规划。请查看新计划。",
            "replan": plan_dict,
        })

    def _handle_preference(self):
        body = self._read_json() or {}
        kind = (body.get("kind") or "preference").strip()
        text = (body.get("text") or "").strip()
        if not text:
            return self._send_json(400, {"error": "text 必需"})
        if kind == "avoid":
            data = self.state.persistent_memory.add_avoidance(text)
        else:
            data = self.state.persistent_memory.add_preference(text)
        return self._send_json(200, {"ok": True, "profile": data})

    def _handle_learn(self):
        body = self._read_json()
        name = (body.get("name") or "").strip()
        key = (body.get("key") or "").strip()
        if not name or not key:
            return self._send_json(400, {"error": "name 和 key 必需"})
        habits = analyze_history(self.state.agent.audit_log_path, min_count=1)
        habit = next((h for h in habits if h["key"] == key), None)
        if habit is None:
            return self._send_json(404, {"error": f"未找到习惯 key={key}"})
        agents_dir = (self.state.default_cwd / "agents").resolve()
        try:
            target = materialize_sub_agent(name, habit, agents_dir)
        except FileExistsError as e:
            return self._send_json(409, {"error": str(e)})
        return self._send_json(200, {"path": str(target.relative_to(self.state.default_cwd))})


def _delayed_shutdown(server: ThreadingHTTPServer) -> None:
    import time
    time.sleep(0.3)
    print("[web] 收到退出请求，正在关闭服务...", flush=True)
    server.shutdown()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenShrimp Web 前端")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cwd", default=".", help="智能体默认工作目录")
    p.add_argument("--prompt", default="OpenShrimp_prompt.md")
    p.add_argument("--model", default=os.getenv("OPENSHRIMP_MODEL", "qwen3-coder:30b"))
    p.add_argument("--ollama-host", default=os.getenv("OPENSHRIMP_OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--no-ollama", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    cwd = Path(args.cwd).resolve()
    agent = OpenShrimp(
        prompt_path=Path(args.prompt).resolve(),
        ollama_host=args.ollama_host,
        model=args.model,
        use_ollama=not args.no_ollama,
        workspace=cwd,
    )
    state = ServerState(agent=agent, ollama_host=args.ollama_host, default_cwd=cwd)
    Handler.state = state

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"[web] OpenShrimp Web 已启动：{url}")
    print("[web] 在浏览器打开上述地址；点击页面右上角“退出服务”可关闭。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] 收到 Ctrl+C，正在关闭...")
        server.shutdown()
    server.server_close()
    print("[web] 已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
