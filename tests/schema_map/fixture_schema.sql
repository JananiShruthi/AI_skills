CREATE TABLE accounts (
  id BIGINT PRIMARY KEY,
  name VARCHAR(160) NOT NULL
);

CREATE TABLE users (
  id BIGINT PRIMARY KEY,
  account_id BIGINT NOT NULL,
  display_name VARCHAR(160) NOT NULL,
  FOREIGN KEY (account_id) REFERENCES accounts(id),
  KEY idx_users_account (account_id)
);

CREATE TABLE emails (
  id BIGINT PRIMARY KEY,
  account_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  subject VARCHAR(255),
  received_at DATETIME NOT NULL,
  FOREIGN KEY (account_id) REFERENCES accounts(id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  KEY idx_emails_user (user_id)
);
