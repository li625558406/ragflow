CREATE DATABASE IF NOT EXISTS rag_flow;
USE rag_flow;

-- 文档分析模板表
CREATE TABLE IF NOT EXISTS document_analysis_template (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    doc_type VARCHAR(64) NOT NULL,
    description TEXT,
    dimensions JSON,
    prompt_templates JSON,
    chunk_merge_rule JSON,
    llm_id VARCHAR(64),
    is_default TINYINT(1) DEFAULT 0,
    is_system TINYINT(1) DEFAULT 0,
    tenant_id VARCHAR(32),
    create_time BIGINT,
    create_date DATETIME,
    update_time BIGINT,
    update_date DATETIME,
    INDEX idx_doc_type (doc_type),
    INDEX idx_is_default (is_default),
    INDEX idx_tenant_id (tenant_id)
);

-- 文档分析结果表
CREATE TABLE IF NOT EXISTS document_analysis_result (
    id VARCHAR(32) PRIMARY KEY,
    document_id VARCHAR(32) NOT NULL,
    template_id VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    progress INT DEFAULT 0,
    result JSON,
    error_message TEXT,
    doc_name VARCHAR(255),
    kb_id VARCHAR(32) NOT NULL,
    tenant_id VARCHAR(32) NOT NULL,
    llm_id VARCHAR(64),
    create_time BIGINT,
    create_date DATETIME,
    update_time BIGINT,
    update_date DATETIME,
    INDEX idx_document_id (document_id),
    INDEX idx_template_id (template_id),
    INDEX idx_status (status),
    INDEX idx_kb_id (kb_id),
    INDEX idx_tenant_id (tenant_id)
);

-- 定时任务表
CREATE TABLE IF NOT EXISTS scheduled_task (
    id VARCHAR(32) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL COMMENT 'task display name',
    description TEXT COMMENT 'task description',
    script_path TEXT NOT NULL COMMENT 'absolute path to Python script',
    script_args TEXT COMMENT 'CLI arguments passed to script',
    schedule_type VARCHAR(16) NOT NULL DEFAULT 'interval' COMMENT 'cron|interval',
    cron_expression VARCHAR(64) DEFAULT '' COMMENT 'cron expr',
    interval_seconds INT DEFAULT 3600 COMMENT 'seconds between runs',
    enabled TINYINT(1) DEFAULT 1 COMMENT 'whether task is active',
    last_run_time BIGINT COMMENT 'timestamp of last execution',
    last_run_status VARCHAR(16) DEFAULT '' COMMENT 'success|fail|running',
    next_run_time BIGINT COMMENT 'computed next execution timestamp',
    timeout INT DEFAULT 3600 COMMENT 'max execution seconds',
    max_retries INT DEFAULT 0 COMMENT 'retry count on failure',
    retry_count INT DEFAULT 0,
    target_url TEXT COMMENT 'crawl target URL',
    llm_id VARCHAR(64) DEFAULT '' COMMENT 'LLM factory for image analysis',
    llm_model_name VARCHAR(128) DEFAULT '' COMMENT 'LLM model name for image analysis',
    kb_id VARCHAR(32) DEFAULT '' COMMENT 'target knowledge base ID',
    access_token TEXT COMMENT 'access token for authenticated crawling',
    create_time BIGINT,
    create_date DATETIME,
    update_time BIGINT,
    update_date DATETIME,
    INDEX idx_tenant_id (tenant_id),
    INDEX idx_enabled_next (enabled, next_run_time),
    INDEX idx_kb_id (kb_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 定时任务日志表
CREATE TABLE IF NOT EXISTS scheduled_task_log (
    id VARCHAR(32) PRIMARY KEY,
    task_id VARCHAR(32) NOT NULL,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running' COMMENT 'running|success|fail',
    start_time BIGINT,
    end_time BIGINT,
    duration DOUBLE COMMENT 'execution duration in seconds',
    output LONGTEXT COMMENT 'stdout captured from script',
    error_msg LONGTEXT COMMENT 'stderr or exception message',
    pid INT COMMENT 'OS process ID',
    create_time BIGINT,
    create_date DATETIME,
    update_time BIGINT,
    update_date DATETIME,
    INDEX idx_task_id (task_id),
    INDEX idx_tenant_id (tenant_id),
    INDEX idx_task_start (task_id, start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 预置分析模板
INSERT INTO document_analysis_template
(id, name, doc_type, description, dimensions, prompt_templates, chunk_merge_rule, is_default, is_system, create_time, create_date, update_time, update_date)
VALUES
('tpl_bid_default', '招标文件分析', 'bid', '适用于招标文件、投标文件分析',
 '["关键条款摘要", "时间节点提醒", "风险/注意事项", "商务条件分析"]',
 '{"key_points": "请提取以下招标文件章节的关键条款摘要，列出最重要的3-5条内容：\n\n{content}", "time_nodes": "请提取以下招标文件章节中的所有时间节点，包括投标截止时间、开标时间、质疑截止时间等，按时间顺序列出：\n\n{content}", "risks": "请分析以下招标文件章节中投标人需要特别注意的风险点和注意事项，列出3-5条：\n\n{content}", "commercial": "请分析以下招标文件章节的商务条件，包括报价要求、付款方式、保证金、质保期等：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW()),

('tpl_contract_default', '合同分析', 'contract', '适用于合同文书分析',
 '["核心条款摘要", "风险/注意事项", "权利义务分析"]',
 '{"key_points": "请提取以下合同章节的核心条款摘要：\n\n{content}", "risks": "请分析以下合同章节的风险点和注意事项：\n\n{content}", "rights": "请分析以下合同章节中甲乙双方的权利和义务：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW()),

('tpl_law_default', '法律文书分析', 'law', '适用于法律法规、法律文书分析',
 '["核心条款摘要", "适用范围", "风险提示"]',
 '{"key_points": "请提取以下法律文书的核心条款：\n\n{content}", "scope": "请分析以下法律文书的适用范围：\n\n{content}", "risks": "请提示以下法律文书的风险注意点：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW());
