-- Explicit operator action only. Additive InnoDB tables; pilot tables untouched.
CREATE TABLE analytics_permanent_policy (
 id INT PRIMARY KEY, revision INT NOT NULL DEFAULT 0,
 approved TINYINT NOT NULL DEFAULT 0, enabled TINYINT NOT NULL DEFAULT 0,
 daily_limit INT NOT NULL DEFAULT 0, cooldown_days INT NOT NULL DEFAULT 0,
 producer_revision INT NOT NULL DEFAULT 0, generator_version VARCHAR(128) NOT NULL DEFAULT ''
) ENGINE=InnoDB;
INSERT INTO analytics_permanent_policy(id) VALUES(1);
CREATE TABLE analytics_permanent_reservations (
 signal_key CHAR(64) PRIMARY KEY, business_date DATE NOT NULL, policy_revision INT NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 INDEX ix_permanent_day(business_date)
) ENGINE=InnoDB;
CREATE TABLE analytics_permanent_policy_events (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(100) NOT NULL, revision INT NOT NULL,
 action VARCHAR(20) NOT NULL, policy_json LONGTEXT NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
