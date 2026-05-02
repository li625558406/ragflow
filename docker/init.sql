CREATE DATABASE IF NOT EXISTS rag_flow;
USE rag_flow;

-- 文档分析模板表
CREATE TABLE IF NOT EXISTS document_analysis_template (
    id VARCHAR(32) PRIMARY KEY,
    f_name VARCHAR(255) NOT NULL,
    f_doc_type VARCHAR(64) NOT NULL,
    f_description TEXT,
    f_dimensions JSON,
    f_prompt_templates JSON,
    f_chunk_merge_rule JSON,
    f_is_default TINYINT(1) DEFAULT 0,
    f_is_system TINYINT(1) DEFAULT 0,
    f_tenant_id VARCHAR(32),
    f_create_time BIGINT,
    f_create_date DATETIME,
    f_update_time BIGINT,
    f_update_date DATETIME,
    INDEX idx_doc_type (f_doc_type),
    INDEX idx_is_default (f_is_default),
    INDEX idx_tenant_id (f_tenant_id)
);

-- 文档分析结果表
CREATE TABLE IF NOT EXISTS document_analysis_result (
    id VARCHAR(32) PRIMARY KEY,
    f_document_id VARCHAR(32) NOT NULL,
    f_template_id VARCHAR(32) NOT NULL,
    f_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    f_progress INT DEFAULT 0,
    f_result JSON,
    f_error_message TEXT,
    f_doc_name VARCHAR(255),
    f_kb_id VARCHAR(32) NOT NULL,
    f_tenant_id VARCHAR(32) NOT NULL,
    f_llm_id VARCHAR(64),
    f_create_time BIGINT,
    f_create_date DATETIME,
    f_update_time BIGINT,
    f_update_date DATETIME,
    INDEX idx_document_id (f_document_id),
    INDEX idx_template_id (f_template_id),
    INDEX idx_status (f_status),
    INDEX idx_kb_id (f_kb_id),
    INDEX idx_tenant_id (f_tenant_id)
);

-- 预置分析模板
INSERT INTO document_analysis_template
(id, f_name, f_doc_type, f_description, f_dimensions, f_prompt_templates, f_chunk_merge_rule, f_is_default, f_is_system, f_create_time, f_create_date, f_update_time, f_update_date)
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