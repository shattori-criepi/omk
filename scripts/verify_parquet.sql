-- Run with: duckdb < scripts/verify_parquet.sql
SELECT 'broute_power' AS dataset, MIN(measured_at), MAX(measured_at), COUNT(*)
FROM read_parquet('data/processed/broute_power/**/*.parquet', hive_partitioning = true);

SELECT date_trunc('hour', measured_at) AS hour, AVG(net_power_w) AS avg_power_w,
       MIN(net_power_w) AS min_power_w, MAX(net_power_w) AS max_power_w
FROM read_parquet('data/processed/broute_power/**/*.parquet', hive_partitioning = true)
GROUP BY hour ORDER BY hour;

SELECT MIN(measured_at), MAX(measured_at), COUNT(*), COUNT(DISTINCT device_id) AS devices,
       COUNT(*) - COUNT(temperature_c) AS temperature_nulls,
       COUNT(*) - COUNT(relative_humidity_pct) AS humidity_nulls,
       COUNT(*) - COUNT(co2_ppm) AS co2_nulls,
       COUNT(*) - COUNT(pm2_5_ug_m3) AS pm25_nulls
FROM read_parquet('data/processed/sen66/**/*.parquet', hive_partitioning = true);
