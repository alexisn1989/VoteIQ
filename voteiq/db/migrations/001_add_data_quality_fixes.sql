-- Migration: Add Data Quality Fixes Tables
-- Date: 2026-05-25
-- Purpose: Implement source conflict detection, bias detection, transparency manifest, and feedback loop

-- Table 1: Representatives (base)
CREATE TABLE IF NOT EXISTS representatives (
    representative_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    state VARCHAR(2) NOT NULL,
    chamber VARCHAR(10) NOT NULL,
    district INT,
    party VARCHAR(20),
    congress_gov_id VARCHAR(255),
    vpap_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_congress_gov (congress_gov_id),
    UNIQUE KEY unique_vpap (vpap_id),
    INDEX idx_state_chamber (state, chamber),
    INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 2: Voting Records
CREATE TABLE IF NOT EXISTS voting_records (
    voting_record_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    bill_id VARCHAR(255) NOT NULL,
    session_year INT NOT NULL,
    vote_position ENUM('yes', 'no', 'abstain', 'present', 'not_voting') NOT NULL,
    bill_title TEXT,
    vote_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    INDEX idx_representative (representative_id),
    INDEX idx_session_year (session_year),
    INDEX idx_bill_id (bill_id),
    UNIQUE KEY unique_vote (representative_id, bill_id, session_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 3: Voting Record Sources (tracks data source for each vote)
CREATE TABLE IF NOT EXISTS voting_record_sources (
    source_id INT PRIMARY KEY AUTO_INCREMENT,
    voting_record_id INT NOT NULL,
    source_name VARCHAR(50) NOT NULL,
    source_position ENUM('yes', 'no', 'abstain', 'present', 'not_voting') NOT NULL,
    yes_rate_percent DECIMAL(5,2),
    data_completeness_percent DECIMAL(5,2),
    last_updated TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (voting_record_id) REFERENCES voting_records(voting_record_id) ON DELETE CASCADE,
    INDEX idx_voting_record (voting_record_id),
    INDEX idx_source (source_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 4: Source Conflicts (Fix #1)
CREATE TABLE IF NOT EXISTS source_conflicts (
    conflict_id INT PRIMARY KEY AUTO_INCREMENT,
    voting_record_id INT NOT NULL,
    source_a_name VARCHAR(50) NOT NULL,
    source_b_name VARCHAR(50) NOT NULL,
    source_a_position VARCHAR(20) NOT NULL,
    source_b_position VARCHAR(20) NOT NULL,
    yes_rate_difference DECIMAL(5,2),
    conflict_type ENUM('position_mismatch', 'scope_mismatch', 'amendment_status', 'revision') NOT NULL,
    severity ENUM('low', 'medium', 'high') NOT NULL,
    investigation_status ENUM('open', 'in_progress', 'resolved', 'no_action_needed') DEFAULT 'open',
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (voting_record_id) REFERENCES voting_records(voting_record_id) ON DELETE CASCADE,
    INDEX idx_voting_record (voting_record_id),
    INDEX idx_severity (severity),
    INDEX idx_status (investigation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 5: Voting Statistics (aggregated per representative/session)
CREATE TABLE IF NOT EXISTS voting_statistics (
    stats_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    session_year INT NOT NULL,
    total_votes INT,
    yes_votes INT,
    no_votes INT,
    yes_rate_percent DECIMAL(5,2),
    party_alignment_percent DECIMAL(5,2),
    contested_vote_alignment DECIMAL(5,2),
    data_completeness_percent DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    UNIQUE KEY unique_stats (representative_id, session_year),
    INDEX idx_representative (representative_id),
    INDEX idx_session (session_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 6: Bias Detection (Fix #6)
CREATE TABLE IF NOT EXISTS bias_detection (
    bias_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    session_year INT NOT NULL,
    bias_type ENUM('selection_bias', 'temporal_confounding', 'simpsons_paradox') NOT NULL,
    detected BOOLEAN DEFAULT FALSE,
    severity ENUM('low', 'medium', 'high') NOT NULL,
    description TEXT,
    mechanism_explanation TEXT,
    recommendation TEXT,
    verification_method TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    INDEX idx_representative (representative_id),
    INDEX idx_bias_type (bias_type),
    INDEX idx_detected (detected),
    UNIQUE KEY unique_bias (representative_id, session_year, bias_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 7: Data Transparency (Fix #4)
CREATE TABLE IF NOT EXISTS data_transparency (
    transparency_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    session_year INT NOT NULL,
    data_sources TEXT,
    source_freshness_status ENUM('CURRENT', 'LAGGING', 'DELAYED', 'UNAVAILABLE') NOT NULL,
    last_updated DATE,
    data_completeness_percent DECIMAL(5,2),
    quality_score INT,
    data_gaps TEXT,
    bias_analysis_summary TEXT,
    recommendation TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    UNIQUE KEY unique_manifest (representative_id, session_year),
    INDEX idx_representative (representative_id),
    INDEX idx_freshness (source_freshness_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 8: Bills Sponsored
CREATE TABLE IF NOT EXISTS bills_sponsored (
    bill_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    bill_number VARCHAR(50),
    bill_title TEXT,
    session_year INT,
    status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    INDEX idx_representative (representative_id),
    INDEX idx_session_year (session_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 9: Committee Assignments
CREATE TABLE IF NOT EXISTS committee_assignments (
    assignment_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT NOT NULL,
    committee_name VARCHAR(255),
    role VARCHAR(50),
    session_year INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    INDEX idx_representative (representative_id),
    INDEX idx_committee (committee_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 10: Search Queries (Fix #3)
CREATE TABLE IF NOT EXISTS search_queries (
    query_id INT PRIMARY KEY AUTO_INCREMENT,
    query_text VARCHAR(500),
    query_type VARCHAR(50),
    results_count INT,
    execution_time_ms INT,
    user_session_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_query (query_text),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 11: User Feedback (Fix #5)
CREATE TABLE IF NOT EXISTS user_feedback (
    feedback_id INT PRIMARY KEY AUTO_INCREMENT,
    feedback_code VARCHAR(20) UNIQUE NOT NULL,
    user_email VARCHAR(255),
    issue_description TEXT NOT NULL,
    issue_type VARCHAR(50),
    representative_id INT,
    suggested_improvement TEXT,
    status ENUM('submitted', 'acknowledged', 'investigating', 'resolved', 'closed') DEFAULT 'submitted',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE SET NULL,
    INDEX idx_code (feedback_code),
    INDEX idx_email (user_email),
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 12: Feedback Investigations (Fix #5)
CREATE TABLE IF NOT EXISTS feedback_investigations (
    investigation_id INT PRIMARY KEY AUTO_INCREMENT,
    feedback_id INT NOT NULL,
    routing_agent VARCHAR(100),
    investigation_notes TEXT,
    root_cause VARCHAR(500),
    resolution_action TEXT,
    verification_completed BOOLEAN DEFAULT FALSE,
    investigation_status ENUM('open', 'in_progress', 'completed', 'escalated') DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (feedback_id) REFERENCES user_feedback(feedback_id) ON DELETE CASCADE,
    INDEX idx_feedback (feedback_id),
    INDEX idx_status (investigation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Table 13: Data Freshness Log
CREATE TABLE IF NOT EXISTS data_freshness_log (
    freshness_id INT PRIMARY KEY AUTO_INCREMENT,
    representative_id INT,
    source_name VARCHAR(100),
    freshness_status ENUM('CURRENT', 'LAGGING', 'DELAYED', 'UNAVAILABLE') NOT NULL,
    last_data_point DATE,
    days_since_update INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_id) REFERENCES representatives(representative_id) ON DELETE CASCADE,
    INDEX idx_representative (representative_id),
    INDEX idx_source (source_name),
    INDEX idx_status (freshness_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- View: Representative Complete Profile
CREATE OR REPLACE VIEW representative_complete_profile AS
SELECT
    r.representative_id,
    r.name,
    r.state,
    r.chamber,
    r.party,
    vs.session_year,
    vs.total_votes,
    vs.yes_rate_percent,
    vs.party_alignment_percent,
    vs.contested_vote_alignment,
    vs.data_completeness_percent,
    dt.quality_score,
    dt.source_freshness_status,
    dt.data_gaps,
    COUNT(DISTINCT sc.conflict_id) as conflict_count,
    COUNT(DISTINCT CASE WHEN bd.detected = TRUE THEN bd.bias_id END) as bias_count
FROM representatives r
LEFT JOIN voting_statistics vs ON r.representative_id = vs.representative_id
LEFT JOIN data_transparency dt ON r.representative_id = dt.representative_id
LEFT JOIN source_conflicts sc ON r.representative_id IN (
    SELECT representative_id FROM voting_records WHERE voting_record_id = sc.voting_record_id
)
LEFT JOIN bias_detection bd ON r.representative_id = bd.representative_id
GROUP BY r.representative_id, vs.session_year;

-- Seed data for initial representatives
INSERT INTO representatives (name, state, chamber, party, congress_gov_id, vpap_id)
VALUES
    ('Jen Kiggans', 'VA', 'House', 'Republican', 'K000389', 'kiggans-jen'),
    ('Jennifer Rouse', 'VA', 'House', 'Democrat', 'R000635', 'rouse-jennifer'),
    ('Virginia Feggans', 'VA', 'House', 'Republican', 'F000789', 'feggans-virginia')
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;
