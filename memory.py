"""OpenShrimp 四层记忆子系统（``memory``）。

本模块实现智能体在对话/任务中可用的四类记忆，全部基于 Python 标准库：

1. :class:`WorkingMemory`  —— 进程内会话记忆，按 ``session_id`` 保存对话轮次，
   支持被前端编辑后“整段覆盖”，从而实现“编辑历史 → 截断 → 重做”。
2. :class:`LongTermMemory` —— 情景记忆，把用户标记“满意”的成功经验追加到
   JSON Lines 文件，并按 token 重合度做轻量召回。
3. :class:`PersistentMemory` —— 用户画像，跨会话持久化偏好与规避事项。
4. :class:`ExternalMemory`   —— 外部知识：本地笔记 RAG（仅基于词频重合）+
   DuckDuckGo Instant Answer API。

顶层辅助：:func:`tokenize`、:func:`overlap_score`、:func:`render_memory_block`。
所有持久化文件默认放在 ``<workspace>/.openshrimp/`` 下，由调用方传入路径。

注：本文件不依赖任何第三方库；分词器对中英文混合做了简单处理（中文按单字
+ 相邻双字 bigram，英文按 ``[A-Za-z0-9_]+`` 分词）。

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ---------- 分词 ----------

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """简单中英文混合分词：英文按词，中文按单字 + 相邻双字。"""
    if not text:
        return []
    raw: list[str] = _TOKEN_RE.findall(text.lower())
    out: list[str] = []
    prev_cn: Optional[str] = None
    for tok in raw:
        out.append(tok)
        if len(tok) == 1 and "\u4e00" <= tok <= "\u9fff":
            if prev_cn is not None:
                out.append(prev_cn + tok)
            prev_cn = tok
        else:
            prev_cn = None
    return out


def overlap_score(query_tokens: Iterable[str], doc_tokens: Iterable[str]) -> int:
    """返回 ``query_tokens`` 与 ``doc_tokens`` 的集合交集大小。

    用作极简的 BM25 替代品：分数越高，越可能命中。零依赖、O(n+m)。
    """
    qset = set(query_tokens)
    if not qset:
        return 0
    dset = set(doc_tokens)
    return len(qset & dset)


# ---------- 工作记忆 ----------

@dataclass
class Turn:
    """对话中的一轮发言。

    Attributes:
        role: ``'user'`` 或 ``'agent'``。
        kind: 类别，如 ``'instruction' | 'plan' | 'result' | 'feedback' | 'note'``。
        text: 文本内容（前端展示文本或后端摘要）。
        ts:   ISO 时间戳，缺省由调用方填充。
        plan_id: 关联的计划 ID（若该轮对应某个具体计划）。
    """

    role: str  # 'user' | 'agent'
    kind: str  # 'instruction' | 'plan' | 'result' | 'feedback' | 'note'
    text: str
    ts: str = ""
    plan_id: Optional[str] = None

    def to_dict(self) -> dict:
        """序列化为可直接 ``json.dumps`` 的 dict。"""
        return {"role": self.role, "kind": self.kind, "text": self.text,
                "ts": self.ts, "plan_id": self.plan_id}


class WorkingMemory:
    """每个会话保存自己的对话轮次，并支持从某一轮截断（用户编辑历史时）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, list[Turn]] = {}

    def get(self, sid: str) -> list[Turn]:
        """返回会话 ``sid`` 的一份轮次快照。永远返回新列表，便于调用方修改。"""
        with self._lock:
            return list(self._sessions.get(sid, []))

    def replace(self, sid: str, turns: list[dict]) -> list[Turn]:
        """以前端传来的对话快照覆盖当前会话。

        该语义是“编辑历史”特性的后端语义：前端可能在某一轮之后丢弃了各轮次，
        后端仅需以其传来的数组为准。
        """
        norm: list[Turn] = []
        for t in turns or []:
            norm.append(Turn(
                role=str(t.get("role", "user")),
                kind=str(t.get("kind", "instruction")),
                text=str(t.get("text", "")),
                ts=str(t.get("ts", "")) or _dt.datetime.now().isoformat(timespec="seconds"),
                plan_id=t.get("plan_id"),
            ))
        with self._lock:
            self._sessions[sid] = norm
        return norm

    def append(self, sid: str, turn: Turn) -> None:
        """在当前会话末尾追加一轮。"""
        with self._lock:
            self._sessions.setdefault(sid, []).append(turn)

    def clear(self, sid: str) -> None:
        """清除会话 ``sid`` 的所有轮次（如存在）。"""
        with self._lock:
            self._sessions.pop(sid, None)


# ---------- 长期记忆（情景） ----------

class LongTermMemory:
    """以 jsonl 形式保存用户确认满意的成功经验，按 token 重合度检索。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add_episode(self, *, purpose: str, command: str, risk_level: str,
                    cwd: str, comment: str = "", tags: Optional[list[str]] = None) -> dict:
        """写入一条成功经验。返回刚写入的记录本身（包含 ``ts``）。"""
        episode = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "purpose": purpose,
            "command": command,
            "risk_level": risk_level,
            "cwd": cwd,
            "comment": comment,
            "tags": tags or [],
        }
        line = json.dumps(episode, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return episode

    def all(self) -> list[dict]:
        """返回所有情景记录（按写入顺序）。文件不存在或损坏时返回空列表。"""
        if not self.path.exists():
            return []
        out: list[dict] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return out

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """按 token 重合度召回与 ``query`` 最相关的前 ``top_k`` 条情景。词频相同时按 ``ts`` 降序。"""
        qtok = tokenize(query)
        if not qtok:
            return []
        scored = []
        for ep in self.all():
            text = (ep.get("purpose", "") + " " + ep.get("command", "")
                    + " " + ep.get("comment", ""))
            s = overlap_score(qtok, tokenize(text))
            if s > 0:
                scored.append((s, ep))
        scored.sort(key=lambda x: (x[0], x[1].get("ts", "")), reverse=True)
        return [ep for _, ep in scored[:top_k]]


# ---------- 持久记忆（用户画像 / 偏好） ----------

class PersistentMemory:
    """跨会话的用户偏好，存储在 profile.json。"""

    DEFAULTS = {
        "language": "zh-CN",
        "shell": "bash",
        "preferences": [],     # 自由文本偏好列表
        "avoid": [],           # 需要规避的事项
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """读取画像；丢失/损坏时返回默认值。返回的 dict 可直接传给保存。"""
        with self._lock:
            if not self.path.exists():
                return dict(self.DEFAULTS)
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return dict(self.DEFAULTS)
                merged = dict(self.DEFAULTS)
                merged.update(data)
                return merged
            except (OSError, json.JSONDecodeError):
                return dict(self.DEFAULTS)

    def save(self, data: dict) -> None:
        """原子覆盖保存画像文件，UTF-8 + indent=2。"""
        with self._lock:
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def add_preference(self, text: str) -> dict:
        """追加一条偏好（去重，最多保留近 50 条）。返回覆盖后的画像。"""
        text = (text or "").strip()
        if not text:
            return self.load()
        data = self.load()
        prefs = list(data.get("preferences", []))
        if text not in prefs:
            prefs.append(text)
        data["preferences"] = prefs[-50:]  # 截断
        self.save(data)
        return data

    def add_avoidance(self, text: str) -> dict:
        """追加一条需规避事项。语义同 :py:meth:`add_preference`。"""
        text = (text or "").strip()
        if not text:
            return self.load()
        data = self.load()
        avoid = list(data.get("avoid", []))
        if text not in avoid:
            avoid.append(text)
        data["avoid"] = avoid[-50:]
        self.save(data)
        return data

    @staticmethod
    def auto_extract(comment: str) -> tuple[Optional[str], Optional[str]]:
        """从用户反馈备注中粗略抽取 偏好 / 规避。返回 (preference, avoidance)。"""
        c = (comment or "").strip()
        if not c:
            return None, None
        pref = None
        avoid = None
        if re.search(r"(请总是|以后总是|偏好|喜欢|默认请|请默认)", c):
            pref = c
        if re.search(r"(不要|不能|禁止|别|避免)", c):
            avoid = c
        return pref, avoid


# ---------- 外部记忆 ----------

class ExternalMemory:
    """本地笔记 RAG + DuckDuckGo 联网搜索。"""

    def __init__(self, notes_dir: Path) -> None:
        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    # -- 本地 RAG --
    def search_notes(self, query: str, top_k: int = 3,
                     max_chars: int = 800) -> list[dict]:
        qtok = tokenize(query)
        if not qtok:
            return []
        results: list[tuple[int, dict]] = []
        for path in self._iter_note_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            score = overlap_score(qtok, tokenize(text))
            if score <= 0:
                continue
            snippet = self._best_snippet(text, qtok, max_chars=max_chars)
            results.append((score, {
                "path": str(path),
                "score": score,
                "snippet": snippet,
            }))
        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[:top_k]]

    def _iter_note_files(self):
        if not self.notes_dir.exists():
            return
        for p in self.notes_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".rst"}:
                yield p

    @staticmethod
    def _best_snippet(text: str, qtok: list[str], max_chars: int) -> str:
        lines = text.splitlines()
        qset = set(qtok)
        scored: list[tuple[int, int]] = []
        for i, ln in enumerate(lines):
            s = overlap_score(qset, tokenize(ln))
            if s > 0:
                scored.append((s, i))
        if not scored:
            return text[:max_chars]
        scored.sort(reverse=True)
        center = scored[0][1]
        start = max(0, center - 4)
        end = min(len(lines), center + 6)
        chunk = "\n".join(lines[start:end])
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars] + "…"
        return chunk

    # -- DuckDuckGo Instant Answer --
    def web_search(self, query: str, timeout: float = 6.0) -> Optional[dict]:
        if not query.strip():
            return None
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            "skip_disambig": "1",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "OpenShrimp/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            return None
        result = {
            "abstract": data.get("AbstractText") or "",
            "abstract_url": data.get("AbstractURL") or "",
            "answer": data.get("Answer") or "",
            "definition": data.get("Definition") or "",
            "related": [],
        }
        for item in (data.get("RelatedTopics") or [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                result["related"].append({
                    "text": item.get("Text", ""),
                    "url": item.get("FirstURL", ""),
                })
        # 全空则返回 None
        if not (result["abstract"] or result["answer"] or result["definition"] or result["related"]):
            return None
        return result


# ---------- 上下文渲染 ----------

def render_memory_block(*,
                        persistent: dict,
                        episodes: list[dict],
                        notes: list[dict],
                        web: Optional[dict]) -> str:
    """把四类记忆拼成给 LLM 的中文上下文片段，可能为空字符串。"""
    parts: list[str] = []

    prefs = (persistent or {}).get("preferences") or []
    avoid = (persistent or {}).get("avoid") or []
    if prefs or avoid:
        parts.append("【用户画像 · 持久偏好】")
        for p in prefs[-8:]:
            parts.append(f"- 偏好：{p}")
        for a in avoid[-8:]:
            parts.append(f"- 规避：{a}")

    if episodes:
        parts.append("\n【长期记忆 · 过往成功经验】")
        for ep in episodes:
            parts.append(f"- 目的：{ep.get('purpose','')}")
            cmd = (ep.get("command") or "").strip().splitlines()[0][:160]
            parts.append(f"  命令：{cmd}")
            if ep.get("comment"):
                parts.append(f"  备注：{ep['comment']}")

    if notes:
        parts.append("\n【外部记忆 · 本地笔记】")
        for n in notes:
            parts.append(f"- 来源：{n.get('path','')}")
            for ln in (n.get("snippet") or "").splitlines():
                if ln.strip():
                    parts.append(f"  | {ln.strip()[:200]}")

    if web:
        parts.append("\n【外部记忆 · 联网搜索】")
        if web.get("answer"):
            parts.append(f"- 直接回答：{web['answer']}")
        if web.get("abstract"):
            parts.append(f"- 摘要：{web['abstract']}")
            if web.get("abstract_url"):
                parts.append(f"  来源：{web['abstract_url']}")
        if web.get("definition"):
            parts.append(f"- 释义：{web['definition']}")
        for r in (web.get("related") or [])[:3]:
            if r.get("text"):
                parts.append(f"- 相关：{r['text']}")

    return "\n".join(parts).strip()
