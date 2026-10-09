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
    (
        2,
        "每张桌子的执行计划与预算豁免",
        """
-- 每张桌子的执行计划（内部使用，含模型 id，不出现在对外视图中）
CREATE TABLE session_tables (
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no            INTEGER NOT NULL,
    plan                TEXT,                         -- 手动模式为空
    pipeline            TEXT NOT NULL,                -- JSON 列表
    members             TEXT NOT NULL,                -- JSON：代号 → 模型 id
    coordinator         TEXT,
    escalate_to         TEXT,
    estimate            TEXT NOT NULL,                -- JSON：总计与各步骤预估
    status              TEXT NOT NULL,                -- pending / approved / running / done
    escalation_reason   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (session_id, table_no)
);

-- 用户在预算卡片上选择"超出预算继续"后，本场讨论不再拦截
ALTER TABLE sessions ADD COLUMN budget_override INTEGER NOT NULL DEFAULT 0;
""",
    ),
    (
        3,
        "保存用户的模式选择",
        """
-- 用户选择（模式、预设、手动勾选的组员与统筹），JSON；路由前中断也能恢复
ALTER TABLE sessions ADD COLUMN choice TEXT NOT NULL DEFAULT '{}';
""",
    ),
    (
        4,
        "匿名开关与工作模式",
        """
-- 匿名开关：开启时揭晓前不显示身份；v2 起默认关闭（之前的会话都是匿名进行的，因此旧行为 1）
ALTER TABLE sessions ADD COLUMN anonymous INTEGER NOT NULL DEFAULT 1;
-- 工作模式：discussion 讨论（作答 → 互评 → 修订 → 汇总）/ collab 协同
ALTER TABLE sessions ADD COLUMN workflow TEXT NOT NULL DEFAULT 'discussion';
-- 自 v2 起 sessions.mode 存成员档位（budget / flagship / custom），preset 不再使用
""",
    ),
    (
        5,
        "成员贡献记录",
        """
-- 每位成员在每张桌子上的贡献（按模型 id 记，为以后按历史表现分工积累数据）
-- kind：answered 作答 / adopted 被采纳的要点 / valid_review 有效评审 / valid_issue 指出的有效问题 /
--       issue_accepted 被作者采纳的问题 / redo 被打回重做 / lazy 被标记敷衍 / dropped 中途退出
CREATE TABLE contributions (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no    INTEGER NOT NULL,
    code        TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, table_no, code, kind)
);
CREATE INDEX idx_contributions_model ON contributions(model_id, kind);
""",
    ),
    (
        6,
        "附件",
        """
-- 用户上传的文件（附件）。上传时 session_id 为空，提交题目时关联到会话（position 为顺序）。
-- 文件本身按内容哈希存放（storage_key = sha256.扩展名）；name 只用于显示。
-- kind：image / pdf / docx / text / audio
-- text：文档提取的文字、图片的文字版（vision 模型生成）或音频转写稿（transcribe 模型生成）
-- text_source：extract / vision / transcribe；status：ready 可用 / pending 待模型处理 / failed
CREATE TABLE attachments (
    id           TEXT PRIMARY KEY,
    session_id   TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    position     INTEGER,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    mime         TEXT NOT NULL,
    ext          TEXT NOT NULL,
    size         INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    storage_key  TEXT NOT NULL,
    pages        INTEGER,
    text         TEXT,
    text_source  TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    warnings     TEXT NOT NULL DEFAULT '[]',   -- JSON
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_attachments_session ON attachments(session_id, position);
""",
    ),
    (
        7,
        "工具调用与成员生成的文件",
        """
-- 成员在作答过程中申请的工具调用（python / write_file / generate_image）
-- round：同一次作答中的第几轮；call_id：提出申请的那次模型调用
-- status：ok / error（程序出错）/ timeout / rejected（不允许或格式不符）/ limit（额度用完）
CREATE TABLE tool_calls (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no    INTEGER NOT NULL,
    step        TEXT NOT NULL,
    code        TEXT,
    round       INTEGER NOT NULL,
    tool        TEXT NOT NULL,
    input       TEXT NOT NULL,
    output      TEXT,
    status      TEXT NOT NULL,
    duration_s  REAL,
    cost_usd    REAL NOT NULL DEFAULT 0,   -- 仅供展示；预算按 calls 表统计
    call_id     INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_tool_calls_session ON tool_calls(session_id);
-- 成员生成的文件；内容按哈希存放（storage_key），path 是 out/ 下清理过的相对路径
-- 同一成员重写同一路径时新增一行，最新的一行为准
CREATE TABLE files (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no     INTEGER NOT NULL,
    step         TEXT NOT NULL,
    code         TEXT,
    tool_call_id INTEGER,
    path         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    mime         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    storage_key  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_files_session ON files(session_id, table_no, code);
""",
    ),
    (
        8,
        "媒体生成任务",
        """
-- 每次媒体生成（图片 / 语音 / 视频）一行；视频是异步任务，external_id / polling_url 存库，
-- 暂停或重启后从数据库继续轮询同一个任务，不会重复提交（不重复计费）。
-- 同一处（桌、步骤、成员、子任务、轮次）重试时 attempt 递增，最后一行为准。
-- state：submitted 已提交 / pending 排队 / running 生成中 / completed / failed / timeout
-- cost_usd 与 calls 表中 role = media 的记录一致；file_id 指向 files 表
CREATE TABLE media_jobs (
    id           INTEGER PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    table_no     INTEGER NOT NULL,
    step         TEXT NOT NULL,
    code         TEXT,
    subtask      TEXT,
    round        INTEGER NOT NULL,
    attempt      INTEGER NOT NULL DEFAULT 1,
    kind         TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    channel      TEXT,
    route_model  TEXT,
    external_id  TEXT,
    polling_url  TEXT,
    state        TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',   -- JSON
    cost_usd     REAL NOT NULL DEFAULT 0,
    error        TEXT,
    file_id      TEXT,
    submitted_at REAL,                          -- Unix 秒，用于计算轮询超时
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX idx_media_jobs_session ON media_jobs(session_id, table_no, step);
""",
    ),
    (
        9,
        "风格参考图与参考图张数",
        """
-- 风格参考图：上传的图片默认是"风格参考"（style_ref = 1），取消勾选后为 0（只是普通附件）。
-- 媒体生成任务记下传给画图模型的参考图张数（0 = 没传，只按文字描述生成）。
ALTER TABLE attachments ADD COLUMN style_ref INTEGER NOT NULL DEFAULT 1;
ALTER TABLE media_jobs ADD COLUMN reference_count INTEGER NOT NULL DEFAULT 0;
""",
    ),
]
