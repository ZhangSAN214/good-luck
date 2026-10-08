"""版本化迁移。只能在末尾追加新版本；已发布的版本不得修改（有测试检查其哈希）。

后续扩展加新表或新列，不改变已有列的含义。
"""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "初始结构",
        """
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    question        TEXT NOT NULL,
    attachments     TEXT NOT NULL DEFAULT '[]',   -- JSON
    seed            INTEGER NOT NULL,
    mode            TEXT NOT NULL,                -- auto / preset / manual
    preset          TEXT,
    status          TEXT NOT NULL,
    error           TEXT,
    revealed_at     TEXT
);
CREATE INDEX idx_sessions_created ON sessions(created_at);

-- 每题一条路由记录：难度判断、方案、预估与实际花费、升级（完整内容在 record JSON）
CREATE TABLE routing_records (
    session_id          TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    difficulty          TEXT,
    difficulty_source   TEXT NOT NULL,
    plan                TEXT,
    estimated_cost_usd  REAL NOT NULL,
    actual_cost_usd     REAL,
    escalated           INTEGER NOT NULL DEFAULT 0,
    escalated_plan      TEXT,
    record              TEXT NOT NULL,            -- JSON
    updated_at          TEXT NOT NULL
);

-- 座位：table_no 0 为初始圆桌，升级后的圆桌为 1、2…；统筹 code 为空
CREATE TABLE seats (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no    INTEGER NOT NULL,
    role        TEXT NOT NULL,                    -- member / coordinator
    code        TEXT,
    model_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, table_no, code)
);
CREATE INDEX idx_seats_role ON seats(role, created_at);

-- 每次模型调用（成功或失败）
CREATE TABLE calls (
    id              INTEGER PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no        INTEGER,                      -- 规划员调用为空
    step            TEXT NOT NULL,
    role            TEXT NOT NULL,                -- planner / member / coordinator
    code            TEXT,
    model_id        TEXT NOT NULL,
    prompt_role     TEXT,
    prompt_version  TEXT,
    prompt_sha256   TEXT,
    input           TEXT NOT NULL,                -- JSON：发送的消息
    output          TEXT,
    channel         TEXT,
    channel_kind    TEXT,
    route_model     TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    cost_source     TEXT,
    latency_s       REAL,
    error           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_calls_session ON calls(session_id);
CREATE INDEX idx_calls_channel ON calls(channel);

-- 每次渠道尝试（用于按渠道统计失败与切换）
CREATE TABLE call_attempts (
    call_id     INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    channel     TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    latency_s   REAL NOT NULL,
    PRIMARY KEY (call_id, seq)
);

-- 各步骤产出：answer / review / revision / synthesis …（以后的步骤也放这里）
CREATE TABLE outputs (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no    INTEGER NOT NULL,
    step        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    code        TEXT,                             -- 作者代号；统筹为空
    content     TEXT NOT NULL,                    -- 文本或 JSON
    call_id     INTEGER REFERENCES calls(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_outputs_session ON outputs(session_id, table_no, step);

-- 已完成的步骤（恢复时跳过）
CREATE TABLE step_progress (
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no        INTEGER NOT NULL,
    step            TEXT NOT NULL,
    completed_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, table_no, step)
);

-- 确认点：卡片内容与用户回复
CREATE TABLE checkpoints (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                    -- cost / escalation / budget …
    card        TEXT NOT NULL,                    -- JSON：现状 / 选项 / 代价 / 推荐
    status      TEXT NOT NULL,                    -- pending / answered
    response    TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL,
    answered_at TEXT
);
CREATE INDEX idx_checkpoints_session ON checkpoints(session_id, status);
""",
    ),
]
