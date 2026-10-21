-- 复现用负载：40 万行订单表，user_id 无索引（典型慢 SQL 形态）。
-- 在任意 MySQL 8.0 / TXSQL 8.0 实例上执行（不要在生产库执行）。
CREATE DATABASE IF NOT EXISTS shop;
USE shop;
DROP TABLE IF EXISTS orders;
CREATE TABLE orders (
  id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id INT, status TINYINT,
  amount DECIMAL(10,2), created_at DATETIME, note VARCHAR(200));
SET SESSION cte_max_recursion_depth = 1000000;
INSERT INTO orders (user_id, status, amount, created_at, note)
WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 400000)
SELECT FLOOR(RAND() * 50000), FLOOR(RAND() * 5), RAND() * 1000,
       NOW() - INTERVAL FLOOR(RAND() * 86400 * 30) SECOND, REPEAT('x', 120) FROM seq;
ANALYZE TABLE orders;
TRUNCATE performance_schema.events_statements_summary_by_digest;
-- 慢：全表扫描 + filesort，5 次（实测 avg ≈ 92ms，examined 40 万 / sent 8）
SELECT id, amount FROM orders WHERE user_id = 101 ORDER BY created_at DESC LIMIT 10;
SELECT id, amount FROM orders WHERE user_id = 202 ORDER BY created_at DESC LIMIT 10;
SELECT id, amount FROM orders WHERE user_id = 303 ORDER BY created_at DESC LIMIT 10;
SELECT id, amount FROM orders WHERE user_id = 404 ORDER BY created_at DESC LIMIT 10;
SELECT id, amount FROM orders WHERE user_id = 505 ORDER BY created_at DESC LIMIT 10;
-- 慢：全表聚合，1 次
SELECT status, COUNT(*), SUM(amount) FROM orders GROUP BY status;
-- 快：主键点查（复现脚本会再循环执行 300 次）
SELECT id FROM orders WHERE id = 1;
