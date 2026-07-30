-- Run with: duckdb < scripts/verify_parquet.sql
SELECT 'broute_power' AS dataset, MIN(measured_at), MAX(measured_at), COUNT(*)
FROM read_parquet('data/processed/broute_power/**/*.parquet', hive_partitioning = true);

SELECT date_trunc('hour', measured_at) AS hour, AVG(net_power_w) AS avg_power_w,
       MIN(net_power_w) AS min_power_w, MAX(net_power_w) AS max_power_w
FROM read_parquet('data/processed/broute_power/**/*.parquet', hive_partitioning = true)
GROUP BY hour ORDER BY hour;

SELECT COUNT(*) AS record_count, MIN(start_at) AS first_start_at, MAX(end_at) AS last_end_at,
       SUM(import_energy_kwh) AS total_import_kwh, SUM(export_energy_kwh) AS total_export_kwh
FROM read_parquet('data/processed/broute_interval_energy/**/*.parquet', hive_partitioning = true);

SELECT quality_status, COUNT(*) AS record_count
FROM read_parquet('data/processed/broute_interval_energy/**/*.parquet', hive_partitioning = true)
GROUP BY quality_status ORDER BY quality_status;

SELECT MIN(measured_at), MAX(measured_at), COUNT(*), COUNT(DISTINCT device_id) AS devices,
       COUNT(*) - COUNT(temperature_c) AS temperature_nulls,
       COUNT(*) - COUNT(relative_humidity_pct) AS humidity_nulls,
       COUNT(*) - COUNT(co2_ppm) AS co2_nulls,
       COUNT(*) - COUNT(pm2_5_ug_m3) AS pm25_nulls
FROM read_parquet('data/processed/sen66/**/*.parquet', hive_partitioning = true);
