-- Postgres 初始化:容器首次起来时自动跑(docker-entrypoint-initdb.d)。
-- 创建测试数据库,跟 dev 数据隔离。
CREATE DATABASE shopmind_test OWNER shopmind;
